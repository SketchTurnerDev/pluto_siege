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

"""Hardware GPS/GNSS receiver module via gpsd TCP client for PlutoSiege.

Polls a local or remote gpsd daemon (default port 2947) in the background
to geotag captured RF bursts in SigMF metadata files.
"""

from __future__ import annotations

import json
import math
import socket
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pluto_siege.config import AppConfig


@dataclass(frozen=True)
class GPSFix:
    """Snapshot of geographic position and receiver status from gpsd."""

    longitude: float = 0.0
    latitude: float = 0.0
    altitude: float = 0.0
    has_fix: bool = False
    satellites: int = 0
    timestamp_utc: str = ""
    speed_knots: float = 0.0
    track_deg: float = 0.0


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integral_number(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _parse_tpv(msg: dict[str, Any], prev: GPSFix) -> GPSFix | None:
    mode = _integral_number(msg.get("mode"))
    if mode is None or not 0 <= mode <= 3:
        return None

    timestamp = prev.timestamp_utc
    if "time" in msg and msg["time"] is not None:
        if not isinstance(msg["time"], str):
            return None
        timestamp = msg["time"]

    # Mode < 2 (no fix) clears stale coordinates so old locations are never reused.
    if mode < 2:
        return GPSFix(satellites=prev.satellites, timestamp_utc=timestamp)

    latitude = _finite_number(msg.get("lat"))
    longitude = _finite_number(msg.get("lon"))
    if (
        latitude is None
        or longitude is None
        or not -90.0 <= latitude <= 90.0
        or not -180.0 <= longitude <= 180.0
    ):
        return None

    altitude = 0.0 if mode == 2 else prev.altitude
    for key in ("alt", "altHAE", "altMSL"):
        if key in msg and msg[key] is not None:
            altitude_value = _finite_number(msg[key])
            if altitude_value is None:
                return None
            altitude = altitude_value
            break

    speed_mps = 0.0
    if "speed" in msg and msg["speed"] is not None:
        raw_speed = _finite_number(msg["speed"])
        if raw_speed is None or raw_speed < 0.0:
            return None
        speed_mps = raw_speed

    track = prev.track_deg
    if "track" in msg and msg["track"] is not None:
        raw_track = _finite_number(msg["track"])
        if raw_track is None or not 0.0 <= raw_track <= 360.0:
            return None
        track = raw_track

    return GPSFix(
        longitude=longitude,
        latitude=latitude,
        altitude=altitude,
        has_fix=True,
        satellites=prev.satellites,
        timestamp_utc=timestamp,
        speed_knots=speed_mps * 1.94384,
        track_deg=track,
    )


def _parse_sky(msg: dict[str, Any], prev: GPSFix) -> GPSFix | None:
    if "uSat" in msg and msg["uSat"] is not None:
        satellites = _integral_number(msg["uSat"])
        if satellites is None or satellites < 0:
            return None
    elif "satellites" in msg and msg["satellites"] is not None:
        raw_satellites = msg["satellites"]
        if not isinstance(raw_satellites, list):
            return None
        satellites = 0
        for satellite in raw_satellites:
            if not isinstance(satellite, dict):
                return None
            satellites += int(satellite.get("used") is True)
    else:
        return None

    return GPSFix(
        longitude=prev.longitude,
        latitude=prev.latitude,
        altitude=prev.altitude,
        has_fix=prev.has_fix,
        satellites=satellites,
        timestamp_utc=prev.timestamp_utc,
        speed_knots=prev.speed_knots,
        track_deg=prev.track_deg,
    )


def parse_gpsd_message(
    msg: dict[str, Any], current_fix: GPSFix | None = None
) -> GPSFix | None:
    """Parse one gpsd JSON message (TPV or SKY) into an updated GPSFix."""
    if not isinstance(msg, dict):
        return None
    prev = current_fix or GPSFix()
    cls = msg.get("class")
    if cls == "TPV":
        return _parse_tpv(msg, prev)
    if cls == "SKY":
        return _parse_sky(msg, prev)
    return None


class GPSManager:
    """Asynchronous background manager connecting to gpsd daemon via TCP."""

    _CONNECT_TIMEOUT = 3.0
    _READ_TIMEOUT = 0.5
    _RECONNECT_DELAY = 1.0
    _MAX_LINE_BYTES = 64 * 1024

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._latest_fix: GPSFix = GPSFix()
        self._status_text: str = "OFF"
        self._connected: bool = False

    def start(self) -> None:
        """Start background gpsd polling thread if not already running."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="PlutoSiege-GPSD", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop background gpsd polling thread and invalidate its last fix."""
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._thread
            active_socket = self._socket
            if active_socket is not None:
                try:
                    active_socket.close()
                except OSError:
                    pass

        if thread is not None and thread.is_alive():
            thread.join(timeout=self._CONNECT_TIMEOUT)
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
        with self._lock:
            self._latest_fix = GPSFix()
            self._status_text = "OFF"
            self._connected = False

    def get_latest_fix(self) -> GPSFix:
        """Return a thread-safe snapshot of the latest GPSFix."""
        with self._lock:
            return self._latest_fix

    def get_status_text(self) -> str:
        """Return short status description for UI rendering."""
        with self._lock:
            return self._status_text

    def _endpoint(self) -> tuple[str, int] | None:
        config = self.config
        host = config.gpsd_host if config is not None else "127.0.0.1"
        port = config.gpsd_port if config is not None else 2947
        if not isinstance(host, str) or not host.strip():
            return None
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            return None
        return host.strip(), port

    def _set_status(self, status: str, connected: bool = False) -> None:
        with self._lock:
            self._status_text = status
            self._connected = connected

    def _invalidate_fix(self, status: str | None = None) -> None:
        with self._lock:
            self._latest_fix = GPSFix()
            self._connected = False
            if status is not None:
                self._status_text = status

    def _update_fix(self, new_fix: GPSFix) -> None:
        with self._lock:
            self._latest_fix = new_fix
            if new_fix.has_fix:
                lat_card = "N" if new_fix.latitude >= 0 else "S"
                lon_card = "E" if new_fix.longitude >= 0 else "W"
                self._status_text = (
                    f"{abs(new_fix.latitude):.3f}°{lat_card}, "
                    f"{abs(new_fix.longitude):.3f}°{lon_card} ({new_fix.satellites} sats)"
                )
            else:
                self._status_text = (
                    f"Searching ({new_fix.satellites} sats)"
                    if new_fix.satellites > 0
                    else "Searching..."
                )

    def _read_socket(self, sock: socket.socket, endpoint: tuple[str, int]) -> None:
        """Read newline-delimited JSON objects from gpsd."""
        buf = b""
        discard_line = False
        while not self._stop_event.is_set() and self._endpoint() == endpoint:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk or not isinstance(chunk, bytes):
                return

            if discard_line:
                nl = chunk.find(b"\n")
                if nl < 0:
                    continue
                chunk = chunk[nl + 1 :]
                discard_line = False

            buf += chunk
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                if len(raw_line) > self._MAX_LINE_BYTES:
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except (
                    ValueError,
                    OverflowError,
                    UnicodeDecodeError,
                    RecursionError,
                    TypeError,
                ):
                    continue
                if not isinstance(message, dict):
                    continue
                with self._lock:
                    current = self._latest_fix
                new_fix = parse_gpsd_message(message, current_fix=current)
                if new_fix is not None:
                    if self._stop_event.is_set() or self._endpoint() != endpoint:
                        return
                    self._update_fix(new_fix)

            if len(buf) > self._MAX_LINE_BYTES:
                buf = b""
                discard_line = True

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                endpoint = self._endpoint()
                if endpoint is None:
                    self._invalidate_fix("Invalid gpsd endpoint")
                    if self._stop_event.wait(self._RECONNECT_DELAY):
                        return
                    continue
                host, port = endpoint
                self._set_status(f"Connecting to gpsd ({host}:{port})...")
                sock: socket.socket | None = None
                connected = False
                try:
                    sock = socket.create_connection(
                        endpoint, timeout=self._CONNECT_TIMEOUT
                    )
                    with self._lock:
                        self._socket = sock
                    if self._stop_event.is_set():
                        return
                    sock.settimeout(self._READ_TIMEOUT)
                    sock.sendall(b'?WATCH={"enable":true,"json":true};\r\n')
                    connected = True
                    self._set_status(
                        "Connected to gpsd (waiting for fix)...", connected=True
                    )
                    self._read_socket(sock, endpoint)
                except OSError:
                    pass
                finally:
                    with self._lock:
                        if self._socket is sock:
                            self._socket = None
                    if sock is not None:
                        try:
                            sock.close()
                        except OSError:
                            pass

                if self._stop_event.is_set():
                    return
                if self._endpoint() != endpoint:
                    self._invalidate_fix("gpsd endpoint changed")
                elif connected:
                    self._invalidate_fix("Lost gpsd connection")
                else:
                    self._invalidate_fix(f"Cannot connect to gpsd ({host}:{port})")
                if self._stop_event.wait(self._RECONNECT_DELAY):
                    return
        finally:
            with self._lock:
                self._latest_fix = GPSFix()
                self._connected = False
                self._status_text = (
                    "OFF" if self._stop_event.is_set() else "GPS worker stopped"
                )
                self._socket = None


_GLOBAL_GPS: GPSManager | None = None
_GPS_LOCK = threading.Lock()


def get_gps_manager() -> GPSManager:
    """Return the global GPSManager singleton."""
    global _GLOBAL_GPS
    with _GPS_LOCK:
        if _GLOBAL_GPS is None:
            _GLOBAL_GPS = GPSManager()
        return _GLOBAL_GPS


def init_gps(cfg: AppConfig) -> GPSManager:
    """Initialize and start or stop the global GPS manager based on configuration."""
    mgr = get_gps_manager()
    mgr.config = cfg
    if cfg.enable_gps:
        mgr.start()
    else:
        mgr.stop()
    return mgr


def stop_gps() -> None:
    """Stop and release the global GPS manager."""
    global _GLOBAL_GPS
    with _GPS_LOCK:
        if _GLOBAL_GPS is not None:
            _GLOBAL_GPS.stop()
            _GLOBAL_GPS = None
