# PlutoSiege - RF capture and replay tool for PlutoSDR.
# Copyright (C) 2026 SketchTurnerDev
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import socket
import threading
import time

from pluto_siege.config import AppConfig
from pluto_siege.gps import GPSFix, GPSManager, parse_gpsd_message


def test_parse_tpv_fix_converts_speed() -> None:
    result = parse_gpsd_message(
        {
            "class": "TPV",
            "mode": 3,
            "lat": 55.75,
            "lon": 37.61,
            "alt": 180.5,
            "time": "2026-01-01T12:00:00Z",
            "speed": 2.0,
            "track": 90.0,
        }
    )

    assert result is not None
    assert result.has_fix is True
    assert result.latitude == 55.75
    assert result.longitude == 37.61
    assert result.speed_knots == 2.0 * 1.94384
    assert result.timestamp_utc == "2026-01-01T12:00:00Z"


def test_parse_tpv_accepts_null_optional_fields_in_2d_fix() -> None:
    result = parse_gpsd_message(
        {
            "class": "TPV",
            "mode": 2,
            "lat": 55.75,
            "lon": 37.61,
            "alt": None,
            "speed": None,
            "track": None,
        }
    )

    assert result is not None
    assert result.has_fix is True
    assert result.latitude == 55.75
    assert result.longitude == 37.61
    assert result.altitude == 0.0


def test_tpv_without_fix_clears_stale_coordinates() -> None:
    previous = GPSFix(longitude=37.61, latitude=55.75, has_fix=True, satellites=4)
    result = parse_gpsd_message({"class": "TPV", "mode": 1}, previous)

    assert result == GPSFix(satellites=4)


def test_parse_sky_updates_satellite_count() -> None:
    previous = GPSFix(latitude=55.75, has_fix=True)
    result = parse_gpsd_message({"class": "SKY", "uSat": 7}, previous)

    assert result is not None
    assert result.satellites == 7
    assert result.latitude == previous.latitude
    assert result.has_fix is True


def test_invalid_tpv_is_rejected() -> None:
    result = parse_gpsd_message({"class": "TPV", "mode": 3, "lat": 91.0, "lon": 0.0})

    assert result is None


def test_gps_manager_validates_endpoint() -> None:
    manager = GPSManager(AppConfig(gpsd_host=" gpsd.local ", gpsd_port=2947))
    assert manager._endpoint() == ("gpsd.local", 2947)

    manager.config = AppConfig(gpsd_host="", gpsd_port=2947)
    assert manager._endpoint() is None


def test_gps_manager_status_for_fix() -> None:
    manager = GPSManager()
    manager._update_fix(GPSFix(has_fix=False, satellites=3))
    assert "Searching" in manager.get_status_text()

    manager._update_fix(
        GPSFix(latitude=-55.75, longitude=37.61, has_fix=True, satellites=8)
    )
    assert "55.750°S, 37.610°E" in manager.get_status_text()


class FakeGpsdSocket:
    """Deterministic socket stub for testing GPSManager._read_socket and _run."""

    def __init__(
        self,
        chunks: list[bytes | BaseException],
        hold_open: threading.Event | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self._hold_open = hold_open
        self.sent: list[bytes] = []
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _bufsize: int) -> bytes:
        if self.closed:
            raise OSError("Socket closed")
        if self._chunks:
            item = self._chunks.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        if self._hold_open is not None:
            self._hold_open.wait(timeout=2.0)
        return b""

    def close(self) -> None:
        self.closed = True
        if self._hold_open is not None:
            self._hold_open.set()


def test_gps_manager_read_socket_handles_fragmentation_timeouts_and_oversized_lines() -> (
    None
):
    manager = GPSManager(AppConfig(gpsd_host="127.0.0.1", gpsd_port=2947))
    endpoint = ("127.0.0.1", 2947)

    oversized_no_newline = b"x" * (GPSManager._MAX_LINE_BYTES + 100)
    chunks: list[bytes | BaseException] = [
        socket.timeout("read timed out"),
        b'{"class":"SKY","uS',
        b'at":6}\n{"class":"TPV",',
        b'"mode":3,"lat":12.345,"lon":67.89,"alt":42.0}\n',
        b"\n   \n{bad json}\n[1, 2, 3]\n",
        oversized_no_newline,
        b'remainder_to_discard\n{"class":"SKY","uSat":9}\n',
        b"",
    ]
    fake_sock = FakeGpsdSocket(chunks)
    manager._read_socket(fake_sock, endpoint)  # type: ignore[arg-type]

    fix = manager.get_latest_fix()
    assert fix.has_fix is True
    assert fix.latitude == 12.345
    assert fix.longitude == 67.89
    assert fix.altitude == 42.0
    assert fix.satellites == 9
    assert manager.get_status_text() == "12.345°N, 67.890°E (9 sats)"


def test_gps_manager_start_and_stop_lifecycle(monkeypatch) -> None:
    hold = threading.Event()
    fake_sock = FakeGpsdSocket(
        chunks=[
            b'{"class":"SKY","uSat":5}\n',
            b'{"class":"TPV","mode":3,"lat":10.0,"lon":20.0,"alt":150.0}\n',
        ],
        hold_open=hold,
    )

    def fake_create_connection(
        address: tuple[str, int], timeout: float = 3.0
    ) -> FakeGpsdSocket:
        assert address == ("127.0.0.1", 2947)
        return fake_sock

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    manager = GPSManager(
        AppConfig(enable_gps=True, gpsd_host="127.0.0.1", gpsd_port=2947)
    )
    manager.start()
    try:
        deadline = time.monotonic() + 2.0
        while not manager.get_latest_fix().has_fix and time.monotonic() < deadline:
            time.sleep(0.01)

        assert fake_sock.sent == [b'?WATCH={"enable":true,"json":true};\r\n']
        fix = manager.get_latest_fix()
        assert fix.has_fix is True
        assert fix.satellites == 5
        assert fix.latitude == 10.0
        assert fix.longitude == 20.0
    finally:
        manager.stop()

    assert fake_sock.closed is True
    assert manager.get_latest_fix() == GPSFix()
    assert manager.get_status_text() == "OFF"
