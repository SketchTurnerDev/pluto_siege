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
