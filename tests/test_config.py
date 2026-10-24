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

import json

from pluto_siege import config
from pluto_siege.constants import FREQ_RANGE_EXTENDED, FREQ_RANGE_IN_SPEC


def test_validate_config_keeps_valid_values_and_defaults_invalid_values() -> None:
    result = config.validate_config(
        {
            "rx_gain": 20.0,
            "sample_rate": 1.5,
            "auto_threshold": 1,
            "unknown": "ignored",
        }
    )

    assert result.rx_gain == 20.0
    assert result.sample_rate == config.AppConfig().sample_rate
    assert result.auto_threshold is True


def test_ip_uri_is_authoritative() -> None:
    result = config.validate_config(
        {"pluto_uri": " ip:10.0.0.2 ", "pluto_ip": "192.168.2.1"}
    )

    assert result.pluto_uri == "ip:10.0.0.2"
    assert result.pluto_ip == "10.0.0.2"


def test_update_keeps_ip_uri_in_sync() -> None:
    result = config.AppConfig(pluto_uri="ip:192.168.2.1")
    result.update({"pluto_ip": "10.0.0.4"})

    assert result.pluto_ip == "10.0.0.4"
    assert result.pluto_uri == "ip:10.0.0.4"


def test_frequency_bounds_follow_configuration() -> None:
    result = config.AppConfig()
    assert result.freq_bounds == FREQ_RANGE_IN_SPEC

    result.permit_out_of_spec_frequency = True
    assert result.freq_bounds == FREQ_RANGE_EXTENDED


def test_settings_round_trip(tmp_path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(path))
    original = config.AppConfig(rx_freq=868_000_000, enable_gps=True)

    assert config.save_settings(original) is True
    loaded = config.load_settings()

    assert loaded.rx_freq == original.rx_freq
    assert loaded.enable_gps is True
    assert json.loads(path.read_text(encoding="utf-8"))["rx_freq"] == 868_000_000
    assert not list(tmp_path.glob(".settings-*.tmp"))


def test_invalid_settings_file_returns_defaults(tmp_path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", str(path))

    loaded = config.load_settings()

    assert isinstance(loaded, config.AppConfig)
    assert loaded.to_dict() == config.AppConfig().to_dict()


def test_save_settings_rejects_non_finite_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "settings.json"))
    settings = config.AppConfig(rx_gain=float("nan"))

    assert config.save_settings(settings) is False
