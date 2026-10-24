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
    CONFIG_FILE,
    FREQ_RANGE_EXTENDED,
    FREQ_RANGE_IN_SPEC,
    POST_TRIGGER_RANGE,
    PRE_TRIGGER_RANGE,
    RX_BUFFER_RANGE,
    RX_GAIN_RANGE,
    SAMPLE_RATE_RANGE,
    SILENCE_RANGE,
    THRESHOLD_RANGE,
    TX_GAIN_RANGE,
)


def _ip_endpoint(uri: object) -> str | None:
    """Extract the host/IP part from an 'ip:...' URI, or None if not an IP URI."""
    if isinstance(uri, str) and uri.startswith("ip:"):
        return uri[3:].strip()
    return None


@dataclass
class AppConfig:
    """Application configuration with range validation and atomic persistence."""

    pluto_uri: str = "usb:"
    pluto_ip: str = "192.168.2.1"
    rx_freq: int = 433_920_000
    tx_freq: int = 433_920_000
    sample_rate: int = 2_100_000
    rx_gain: float = 15.0
    tx_gain: float = 0.0
    rx_buffer_size: int = 32768
    auto_threshold: bool = True
    auto_trigger_margin: float = 8.0
    manual_threshold: float = -30.0
    pre_trigger_buffers: int = 2
    silence_seconds: float = 0.8
    max_post_trigger_seconds: float = 4.0
    permit_out_of_spec_frequency: bool = False
    enable_mouse: bool = True
    enable_sound: bool = True
    enable_gps: bool = False
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947

    def __post_init__(self) -> None:
        endpoint = _ip_endpoint(self.pluto_uri)
        if endpoint:
            self.pluto_ip = endpoint

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
        if (
            "pluto_ip" in known
            and "pluto_uri" not in known
            and self.pluto_uri.startswith("ip:")
            and isinstance(known["pluto_ip"], str)
            and known["pluto_ip"].strip()
        ):
            candidate["pluto_uri"] = f"ip:{known['pluto_ip'].strip()}"

        validated = validate_config(candidate)
        for f in fields(self):
            setattr(self, f.name, getattr(validated, f.name))

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        endpoint = _ip_endpoint(values["pluto_uri"])
        if endpoint:
            values["pluto_ip"] = endpoint
        elif endpoint == "" and isinstance(values["pluto_ip"], str):
            ip = values["pluto_ip"].strip()
            if ip:
                values["pluto_uri"] = f"ip:{ip}"
        return values

    @property
    def freq_bounds(self) -> tuple[int, int]:
        return (
            FREQ_RANGE_EXTENDED
            if self.permit_out_of_spec_frequency
            else FREQ_RANGE_IN_SPEC
        )


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
        cfg.pluto_uri = uri.strip()

    raw_ip = raw.get("pluto_ip")
    saved_ip = raw_ip.strip() if isinstance(raw_ip, str) and raw_ip.strip() else None
    endpoint = _ip_endpoint(cfg.pluto_uri)
    if endpoint:
        cfg.pluto_ip = endpoint
    elif endpoint == "" and saved_ip:
        cfg.pluto_ip = saved_ip
        cfg.pluto_uri = f"ip:{saved_ip}"
    elif saved_ip:
        cfg.pluto_ip = saved_ip

    for bool_key in (
        "permit_out_of_spec_frequency",
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

    def check_num(key: str, limits: tuple[float, float], cast_type: type) -> None:
        val = raw.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return
        try:
            num = float(val)
        except (OverflowError, ValueError):
            return
        if not math.isfinite(num) or not (limits[0] <= num <= limits[1]):
            return
        if cast_type is int:
            if num.is_integer():
                setattr(cfg, key, int(num))
        else:
            setattr(cfg, key, num)

    freq_range = cfg.freq_bounds
    check_num("rx_freq", freq_range, int)
    check_num("tx_freq", freq_range, int)
    check_num("sample_rate", SAMPLE_RATE_RANGE, int)
    check_num("rx_gain", RX_GAIN_RANGE, float)
    check_num("tx_gain", TX_GAIN_RANGE, float)
    check_num("rx_buffer_size", RX_BUFFER_RANGE, int)
    check_num("pre_trigger_buffers", PRE_TRIGGER_RANGE, int)
    check_num("silence_seconds", SILENCE_RANGE, float)
    check_num("max_post_trigger_seconds", POST_TRIGGER_RANGE, float)
    check_num("manual_threshold", THRESHOLD_RANGE, float)
    check_num("auto_trigger_margin", AUTO_MARGIN_RANGE, float)
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


def freq_bounds(cfg: AppConfig | None = None) -> tuple[int, int]:
    """Return active frequency limits for the given or global configuration."""
    return (CONFIG if cfg is None else cfg).freq_bounds


DEFAULT_SETTINGS = AppConfig().to_dict()
CONFIG = load_settings()
