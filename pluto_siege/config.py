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

"""Configuration management using a validated dataclass."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from typing import Any

from pluto_siege.constants import (
    AUTO_MARGIN_RANGE,
    AUTO_RECAL_RANGE,
    CONFIG_FILE,
    FREQ_RANGE,
    MIN_BURST_RANGE,
    POST_TRIGGER_RANGE,
    PRE_TRIGGER_RANGE,
    RECORDS_DIR,
    RF_BANDWIDTH_RANGE,
    RX_BUFFER_RANGE,
    RX_GAIN_RANGE,
    SAMPLE_RATE_RANGE,
    SILENCE_RANGE,
    THRESHOLD_RANGE,
    TX_GAIN_RANGE,
    TX_REPEATS_RANGE,
)


def normalize_pluto_uri(raw: str) -> str:
    """Validate and normalize a libiio context URI (e.g. 'usb:', 'ip:192.168.2.1')."""
    cleaned = raw.strip()
    if (
        not cleaned
        or "\x00" in cleaned
        or any(ch.isspace() or not ch.isprintable() for ch in cleaned)
    ):
        raise ValueError("Invalid Pluto URI.")
    lower = cleaned.lower()
    if lower in ("usb", "usb:"):
        return "usb:"
    if lower in ("local", "local:"):
        return "local:"
    if lower.startswith("ip:"):
        host = cleaned[3:].strip()
        if not host:
            raise ValueError("IP URI requires a host (e.g. ip:192.168.2.1).")
        return f"ip:{host}"
    if ":" not in cleaned:
        return f"ip:{cleaned}"
    return cleaned


@dataclass
class AppConfig:
    """Application configuration with range validation and atomic persistence."""

    pluto_uri: str = "usb:"
    rx_freq: int = 433_920_000
    sample_rate: int = 2_100_000
    rf_bandwidth: int = 0
    rx_gain: float = 15.0
    tx_gain: float = 0.0
    tx_repeats: int = 1
    rx_buffer_size: int = 32768
    auto_capture: bool = False
    auto_threshold: bool = True
    auto_trigger_margin: float = 8.0
    auto_recal_seconds: float = 0.0
    manual_threshold: float = -30.0
    pre_trigger_buffers: int = 2
    min_burst_seconds: float = 0.0
    silence_seconds: float = 0.8
    max_post_trigger_seconds: float = 4.0
    enable_mouse: bool = True
    enable_sound: bool = True
    enable_gps: bool = False
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947
    records_dir: str = RECORDS_DIR

    def update(self, new_values: dict[str, Any]) -> None:
        """Validate and apply known configuration keys in place."""
        if not isinstance(new_values, dict):
            return
        valid_keys = {f.name for f in fields(self)}
        known = {k: v for k, v in new_values.items() if k in valid_keys}
        if not known:
            return

        candidate = self.to_dict()
        candidate.update(known)
        validated = validate_config(candidate)
        for f in fields(self):
            setattr(self, f.name, getattr(validated, f.name))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_config(raw: dict[str, Any]) -> AppConfig:
    """Validate raw dictionary and return an AppConfig instance.

    Invalid individual fields fall back to their defaults so a partially edited
    settings file preserves all valid values.
    """
    cfg = AppConfig()
    if not isinstance(raw, dict):
        return cfg

    uri = raw.get("pluto_uri")
    if isinstance(uri, str) and uri.strip():
        try:
            cfg.pluto_uri = normalize_pluto_uri(uri)
        except ValueError:
            pass

    for bool_key in (
        "auto_capture",
        "auto_threshold",
        "enable_mouse",
        "enable_sound",
        "enable_gps",
    ):
        val = raw.get(bool_key)
        if isinstance(val, bool):
            setattr(cfg, bool_key, val)

    host = raw.get("gpsd_host")
    if isinstance(host, str) and host.strip():
        cfg.gpsd_host = host.strip()

    rec_dir = raw.get("records_dir")
    if (
        isinstance(rec_dir, str)
        and rec_dir.strip()
        and "\x00" not in rec_dir
        and all(ch.isprintable() for ch in rec_dir.strip())
    ):
        cfg.records_dir = os.path.expanduser(rec_dir.strip())

    def check_num(
        key: str, limits: tuple[float, float | None], cast_type: type
    ) -> None:
        val = raw.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return
        try:
            num = float(val)
        except (OverflowError, ValueError):
            return
        lo, hi = limits
        if not math.isfinite(num) or num < lo or (hi is not None and num > hi):
            return
        if cast_type is int:
            if num.is_integer():
                setattr(cfg, key, int(num))
        else:
            setattr(cfg, key, num)

    check_num("rx_freq", FREQ_RANGE, int)
    check_num("sample_rate", SAMPLE_RATE_RANGE, int)
    raw_bw = raw.get("rf_bandwidth")
    if not isinstance(raw_bw, bool) and isinstance(raw_bw, (int, float)):
        try:
            bw_num = float(raw_bw)
        except (OverflowError, ValueError):
            bw_num = -1.0
        if math.isfinite(bw_num) and bw_num.is_integer():
            bw_int = int(bw_num)
            if bw_int == 0 or RF_BANDWIDTH_RANGE[0] <= bw_int <= RF_BANDWIDTH_RANGE[1]:
                cfg.rf_bandwidth = bw_int
    check_num("rx_gain", RX_GAIN_RANGE, float)
    check_num("tx_gain", TX_GAIN_RANGE, float)
    check_num("tx_repeats", TX_REPEATS_RANGE, int)
    check_num("rx_buffer_size", RX_BUFFER_RANGE, int)
    check_num("pre_trigger_buffers", PRE_TRIGGER_RANGE, int)
    check_num("min_burst_seconds", MIN_BURST_RANGE, float)
    check_num("silence_seconds", SILENCE_RANGE, float)
    check_num("max_post_trigger_seconds", POST_TRIGGER_RANGE, float)
    check_num("manual_threshold", THRESHOLD_RANGE, float)
    check_num("auto_trigger_margin", AUTO_MARGIN_RANGE, float)
    check_num("auto_recal_seconds", AUTO_RECAL_RANGE, float)
    check_num("gpsd_port", (1, 65535), int)

    return cfg


def load_settings() -> AppConfig:
    """Load and validate settings from CONFIG_FILE, or return defaults."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return validate_config(json.load(f))
        except (
            json.JSONDecodeError,
            OSError,
            UnicodeDecodeError,
            ValueError,
            RecursionError,
        ):
            pass
    return AppConfig()


def save_settings(config: AppConfig | None = None) -> bool:
    """Atomically write configuration to CONFIG_FILE."""
    target = CONFIG if config is None else config
    tmp_path: str | None = None
    try:
        payload = json.dumps(target.to_dict(), indent=2, allow_nan=False)
        directory = os.path.dirname(os.path.abspath(CONFIG_FILE))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".settings-", suffix=".tmp", dir=directory
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_FILE)
        tmp_path = None
        return True
    except (OSError, TypeError, ValueError, AttributeError):
        return False
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def parse_number(
    raw: str,
    cast_type: type,
    min_val: float | None = None,
    max_val: float | None = None,
) -> int | float:
    """Parse numeric input string with unit multipliers (k, M, G, Hz, SPS, ms, s).

    Auto-converts bare MHz shorthand (< 10_000, e.g. 433 or 433.92) to Hz/SPS
    for frequency and sample-rate fields.
    """
    cleaned = raw.strip().lower()
    mult = 1.0
    explicit_unit = False
    for suffix, factor in (
        ("ghz", 1e9),
        ("g", 1e9),
        ("msps", 1e6),
        ("mhz", 1e6),
        ("ms", 1e-3),
        ("m", 1e6),
        ("ksps", 1e3),
        ("khz", 1e3),
        ("k", 1e3),
        ("sps", 1.0),
        ("hz", 1.0),
        ("sec", 1.0),
        ("s", 1.0),
    ):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            mult = factor
            explicit_unit = True
            break

    try:
        num = float(cleaned)
    except (ValueError, OverflowError):
        raise ValueError("Invalid number.") from None

    if not math.isfinite(num):
        raise ValueError("Value must be finite.")

    num *= mult
    if not math.isfinite(num):
        raise ValueError("Value must be finite.")

    is_freq_or_rate = min_val is not None and (min_val >= 100_000 or max_val is None)
    if cast_type is int and not explicit_unit and is_freq_or_rate and 0 < num < 10_000:
        num *= 1e6
        if not math.isfinite(num):
            raise ValueError("Value must be finite.")

    if cast_type is int:
        rounded = round(num)
        if abs(num - rounded) > 1e-6:
            raise ValueError("Value must be an integer.")
        return int(rounded)
    return num


def parse_rf_bandwidth(raw: str) -> int:
    """Parse RF filter bandwidth string ('0', 'auto', or Hz with k/M suffixes)."""
    cleaned = raw.strip().lower()
    if cleaned in ("0", "auto"):
        return 0
    lo, hi = RF_BANDWIDTH_RANGE
    val = int(parse_number(raw, int, lo, hi))
    if val < lo or val > hi:
        raise ValueError(f"Must be 0 (auto) or within {lo} .. {hi}")
    return val


DEFAULT_SETTINGS = AppConfig().to_dict()
CONFIG = load_settings()
