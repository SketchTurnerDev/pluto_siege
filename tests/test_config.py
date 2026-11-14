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

import contextlib
import io
import json

import numpy as np
import pytest

from pluto_siege import cli, config
from pluto_siege.constants import RECORDS_DIR
from pluto_siege.sigmf import save_sigmf_pair


def test_validate_config_records_dir() -> None:
    custom = config.validate_config({"records_dir": "  /mnt/usb/captures  "})
    assert custom.records_dir == "/mnt/usb/captures"

    blank = config.validate_config({"records_dir": "   "})
    assert blank.records_dir == RECORDS_DIR


def test_validate_config_keeps_valid_values_and_defaults_invalid_values() -> None:
    result = config.validate_config(
        {
            "rx_gain": 20.0,
            "sample_rate": 1.5,
            "auto_capture": True,
            "auto_threshold": 1,
            "unknown": "ignored",
        }
    )

    assert result.rx_gain == 20.0
    assert result.sample_rate == config.AppConfig().sample_rate
    assert result.auto_capture is True
    assert result.auto_threshold is True
    assert config.validate_config({"auto_capture": "yes"}).auto_capture is False


def test_normalize_and_validate_pluto_uri() -> None:
    assert config.normalize_pluto_uri(" usb: ") == "usb:"
    assert config.normalize_pluto_uri("usb") == "usb:"
    assert config.normalize_pluto_uri("usb:1.4.5") == "usb:1.4.5"
    assert config.normalize_pluto_uri("local") == "local:"
    assert config.normalize_pluto_uri("ip:10.0.0.2") == "ip:10.0.0.2"
    assert config.normalize_pluto_uri("10.0.0.4") == "ip:10.0.0.4"

    for bad_uri in ("", "   ", "ip:", "bad uri"):
        with pytest.raises(ValueError):
            config.normalize_pluto_uri(bad_uri)

    assert config.validate_config({"pluto_uri": " 10.0.0.2 "}).pluto_uri == "ip:10.0.0.2"
    assert config.validate_config({"pluto_uri": "ip:"}).pluto_uri == "usb:"


def test_validate_config_enforces_frequency_range() -> None:
    default_freq = config.AppConfig().rx_freq
    assert config.validate_config({"rx_freq": 70_000_000}).rx_freq == 70_000_000
    assert config.validate_config({"rx_freq": 315_000_000}).rx_freq == 315_000_000
    assert config.validate_config({"rx_freq": 6_000_000_000}).rx_freq == 6_000_000_000
    for bad_freq in (0, -10, 433, 27_000_000, 69_999_999, 6_000_000_001, 7_000_000_000):
        assert config.validate_config({"rx_freq": bad_freq}).rx_freq == default_freq

    # CLI enforces 70 MHz .. 6 GHz while keeping bare MHz shorthand (e.g. 433 -> 433 MHz)
    _, ok_short = cli.parse_cli_args(["--rf", "433"])
    assert ok_short.rx_freq == 433_000_000
    _, ok_low = cli.parse_cli_args(["--rf", "70M"])
    assert ok_low.rx_freq == 70_000_000
    _, ok_high = cli.parse_cli_args(["--rf", "6G"])
    assert ok_high.rx_freq == 6_000_000_000

    for bad_arg in ("433Hz", "50M", "69.9M", "6.1G", "1"):
        with pytest.raises(SystemExit):
            cli.parse_cli_args(["--rx-freq", bad_arg])


def test_validate_and_parse_rf_bandwidth() -> None:
    assert config.validate_config({"rf_bandwidth": 0}).rf_bandwidth == 0
    assert config.validate_config({"rf_bandwidth": 200_000}).rf_bandwidth == 200_000
    assert config.validate_config({"rf_bandwidth": 56_000_000}).rf_bandwidth == 56_000_000
    assert config.validate_config({"rf_bandwidth": 100_000}).rf_bandwidth == 0
    assert config.validate_config({"rf_bandwidth": -1}).rf_bandwidth == 0
    assert config.validate_config({"rf_bandwidth": 60_000_000}).rf_bandwidth == 0
    assert config.validate_config({"rf_bandwidth": True}).rf_bandwidth == 0

    assert config.parse_rf_bandwidth("0") == 0
    assert config.parse_rf_bandwidth("auto") == 0
    assert config.parse_rf_bandwidth("500k") == 500_000
    assert config.parse_rf_bandwidth("2.5M") == 2_500_000
    assert config.parse_rf_bandwidth("56M") == 56_000_000


def test_validate_tx_repeats_and_min_burst_seconds() -> None:
    assert config.validate_config({"tx_repeats": 5}).tx_repeats == 5
    assert config.validate_config({"tx_repeats": 0}).tx_repeats == 1
    assert config.validate_config({"tx_repeats": 101}).tx_repeats == 1
    assert config.validate_config({"rx_gain": -10.0}).rx_gain == -10.0
    assert config.validate_config({"rx_buffer_size": 262_144}).rx_buffer_size == 262_144
    assert config.validate_config({"pre_trigger_buffers": 30}).pre_trigger_buffers == 30
    assert config.validate_config({"silence_seconds": 0.02}).silence_seconds == 0.02
    assert (
        config.validate_config({"max_post_trigger_seconds": 20.0}).max_post_trigger_seconds
        == 20.0
    )
    assert config.validate_config({"min_burst_seconds": 0.0}).min_burst_seconds == 0.0
    assert config.validate_config({"min_burst_seconds": 0.02}).min_burst_seconds == 0.02
    assert config.validate_config({"min_burst_seconds": -0.1}).min_burst_seconds == 0.0
    assert config.validate_config({"min_burst_seconds": 6.0}).min_burst_seconds == 0.0
    assert config.validate_config({"auto_recal_seconds": 0.0}).auto_recal_seconds == 0.0
    assert config.validate_config({"auto_recal_seconds": 15.0}).auto_recal_seconds == 15.0
    assert config.validate_config({"auto_recal_seconds": 600.0}).auto_recal_seconds == 600.0
    assert config.validate_config({"auto_recal_seconds": -1.0}).auto_recal_seconds == 0.0
    assert config.validate_config({"auto_recal_seconds": 601.0}).auto_recal_seconds == 0.0
    assert config.parse_number("15ms", float, 0.0, 5.0) == 0.015
    assert config.parse_number("0.5s", float, 0.0, 5.0) == 0.5


def test_parse_number_mhz_shorthand_and_strict_integers() -> None:
    assert config.parse_number("433", int, 1, None) == 433_000_000
    assert config.parse_number("868", int, 1, None) == 868_000_000
    assert config.parse_number("433.92", int, 1, None) == 433_920_000
    assert config.parse_number("27000hz", int, 1, None) == 27_000
    assert config.parse_number("2", int, 521_000, 61_440_000) == 2_000_000
    assert config.parse_number("1024", int, 1024, 1_048_576) == 1024
    assert config.parse_number("2947", int, 1, 65535) == 2947

    for bad_int in ("1.5", "0.00005"):
        with pytest.raises(ValueError, match="integer"):
            config.parse_number(bad_int, int, 1, 100)


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


def test_parse_cli_args_overrides_and_save_flag(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_file))
    base = config.AppConfig()

    opts, parsed = cli.parse_cli_args(
        [
            "-c",
            "--ac",
            "true",
            "--hl",
            "--pu",
            "10.0.0.5",
            "--rf",
            "100M",
            "--sr",
            "2.5M",
            "--rb",
            "500k",
            "--rg",
            "25",
            "--tg",
            "-10",
            "--tr",
            "3",
            "--mt",
            "-22",
            "--atm",
            "10",
            "--ars",
            "20s",
            "--ptb",
            "4",
            "--mbs",
            "15ms",
            "--ss",
            "0.5",
            "--mpts",
            "3.0",
            "--rbs",
            "16384",
            "--eg",
            "true",
            "--gh",
            "192.168.1.50",
            "--gp",
            "2948",
            "--rd",
            str(tmp_path / "custom_recs"),
            "--es",
            "false",
            "--em",
            "false",
        ],
        base_config=base,
    )

    assert opts.mode == "capture"
    assert opts.headless is True
    assert opts.save_config is False
    assert parsed.auto_capture is True
    assert parsed.pluto_uri == "ip:10.0.0.5"
    assert parsed.rx_freq == 100_000_000
    assert parsed.sample_rate == 2_500_000
    assert parsed.rf_bandwidth == 500_000
    assert parsed.rx_gain == 25.0
    assert parsed.tx_gain == -10.0
    assert parsed.tx_repeats == 3
    assert parsed.manual_threshold == -22.0
    assert parsed.auto_threshold is False
    assert parsed.auto_trigger_margin == 10.0
    assert parsed.auto_recal_seconds == 20.0
    assert parsed.pre_trigger_buffers == 4
    assert parsed.min_burst_seconds == 0.015
    assert parsed.silence_seconds == 0.5
    assert parsed.max_post_trigger_seconds == 3.0
    assert parsed.rx_buffer_size == 16384
    assert parsed.enable_gps is True
    assert parsed.gpsd_host == "192.168.1.50"
    assert parsed.gpsd_port == 2948
    assert parsed.records_dir == str(tmp_path / "custom_recs")
    assert parsed.enable_sound is False
    assert parsed.enable_mouse is False

    # Without --save, settings.json is not created
    cli.apply_cli_to_global_config(opts, parsed)
    assert not cfg_file.exists()

    # With --save / -s, settings.json is persisted
    opts_save, parsed_save = cli.parse_cli_args(
        ["-c", "--rf", "433.92M", "-s"],
        base_config=base,
    )
    assert opts_save.mode == "capture"
    assert opts_save.save_config is True
    cli.apply_cli_to_global_config(opts_save, parsed_save)
    assert cfg_file.exists()
    saved_data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved_data["rx_freq"] == 433_920_000
    assert "tx_freq" not in saved_data
    assert "pluto_ip" not in saved_data

    # Explicit --pluto-uri, --atm, and --ars re-enabling auto_threshold
    manual_base = config.AppConfig(auto_threshold=False)
    _, uri_parsed = cli.parse_cli_args(
        ["--pluto-uri", "usb:1.4.5", "--atm", "12", "--rf", "868"],
        base_config=manual_base,
    )
    assert uri_parsed.pluto_uri == "usb:1.4.5"
    assert uri_parsed.auto_threshold is True
    assert uri_parsed.auto_trigger_margin == 12.0
    assert uri_parsed.rx_freq == 868_000_000

    _, ars_parsed = cli.parse_cli_args(
        ["--ars", "15"],
        base_config=manual_base,
    )
    assert ars_parsed.auto_threshold is True
    assert ars_parsed.auto_recal_seconds == 15.0


def test_cli_list_and_index_resolve(tmp_path) -> None:
    rec_dir = tmp_path / "recs"
    rec_dir.mkdir()
    base = config.AppConfig(records_dir=str(rec_dir))

    opts, parsed = cli.parse_cli_args(["-l", "--rd", str(rec_dir)], base_config=base)
    assert opts.list_recordings is True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.list_recordings(parsed) == 0
    assert "No recordings found" in buf.getvalue()

    samples = np.ones(2100, dtype=np.complex64) * 100.0
    b1 = str(rec_dir / "rec_20261025_100000_433920000")
    b2 = str(rec_dir / "rec_20261026_120000_868350000")
    save_sigmf_pair(
        b1,
        samples,
        freq=433_920_000,
        sr=2_100_000,
        timestamp_iso="2026-10-25T10:00:00Z",
        hw_model="PlutoSDR",
    )
    save_sigmf_pair(
        b2,
        samples,
        freq=868_350_000,
        sr=2_100_000,
        timestamp_iso="2026-10-26T12:00:00Z",
        hw_model="PlutoSDR",
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.list_recordings(parsed) == 0
    out = buf.getvalue()
    assert "[1]" in out and "rec_20261026_120000_868350000.sigmf-data" in out
    assert "[2]" in out and "rec_20261025_100000_433920000.sigmf-data" in out
    assert "868.350 MHz" in out and "433.920 MHz" in out

    assert cli.resolve_transmit_target("1", str(rec_dir)) == f"{b2}.sigmf-data"
    assert cli.resolve_transmit_target("2", str(rec_dir)) == f"{b1}.sigmf-data"
    assert cli.resolve_transmit_target("latest", str(rec_dir)) == f"{b2}.sigmf-data"

    for bad_idx in ("3", "0"):
        with pytest.raises(FileNotFoundError, match="out of range"):
            cli.resolve_transmit_target(bad_idx, str(rec_dir))


def test_cli_show_config_and_reset(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(config, "CONFIG", config.AppConfig())
    monkeypatch.setattr(cli, "CONFIG", config.CONFIG)

    cli.main(["--pu", "ip:10.0.0.9", "--rf", "868.35M", "--rg", "35", "-s"])
    assert cfg_file.exists()
    assert config.CONFIG.pluto_uri == "ip:10.0.0.9"
    assert config.CONFIG.rx_freq == 868_350_000
    assert config.CONFIG.rx_gain == 35.0

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(["--sc"])
    shown = buf.getvalue()
    assert "saved on disk" in shown
    assert "[Radio & Connection]" in shown
    assert "--pluto-uri, --pu" in shown
    assert "--auto-capture, --ac" in shown
    assert "--auto-recal-seconds, --ars" in shown
    assert "ip:10.0.0.9" in shown
    assert "868350000  (868.350 MHz)" in shown

    cli.main(["-r"])
    restored = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert restored == config.AppConfig().to_dict()
    assert config.CONFIG.to_dict() == config.AppConfig().to_dict()

    cli.main(["--reset", "--rf", "315M"])
    restored_with_override = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert restored_with_override["pluto_uri"] == "usb:"
    assert restored_with_override["rx_freq"] == 315_000_000


def test_cli_help_uses_bold_white_headings_and_plain_flags(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    help_text = cli.build_arg_parser().format_help()
    assert "\033[1;37moptions:\033[0m" in help_text
    assert "\033[1;37mOperating modes (mutually exclusive):\033[0m" in help_text
    assert "\033[36m--help\033[0m" in help_text
    assert "\033[1;36m--help" not in help_text
    assert "\033[33mURI\033[0m" in help_text
    assert "\033[1;33mURI" not in help_text





