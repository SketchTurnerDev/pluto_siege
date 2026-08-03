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

"""Configuration management using a strongly-typed dataclass."""

from dataclasses import asdict, dataclass
import json
import math
import os
from typing import Any, Dict, Tuple

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


@dataclass
class AppConfig:
    """Strongly-typed application configuration with validation and dict-like access."""

    pluto_uri: str = "ip:192.168.2.1"
    rx_freq: int = 433_920_000
    tx_freq: int = 433_920_000
    sample_rate: int = 2_000_000
    rx_gain: float = 50.0
    tx_gain: float = -50.0
    rx_buffer_size: int = 32768
    auto_threshold: bool = True
    auto_trigger_margin: float = 8.0
    manual_threshold: float = -30.0
    pre_trigger_buffers: int = 2
    silence_seconds: float = 0.3
    max_post_trigger_seconds: float = 2.0
    permit_out_of_spec_frequency: bool = False

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def __setitem__(self, key: str, value: Any) -> None:
        if not hasattr(self, key):
            raise KeyError(f"Invalid setting key: {key}")
        setattr(self, key, value)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def items(self):
        return asdict(self).items()

    def update(self, new_values: Dict[str, Any]) -> None:
        for k, v in new_values.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def freq_bounds(self) -> Tuple[int, int]:
        return (
            FREQ_RANGE_EXTENDED
            if self.permit_out_of_spec_frequency
            else FREQ_RANGE_IN_SPEC
        )


def validate_config(s: Dict[str, Any]) -> AppConfig:
    """Validate raw configuration dictionary and return an AppConfig instance."""
    cfg = AppConfig()
    if not isinstance(s, dict):
        return cfg

    if isinstance(s.get("pluto_uri"), str) and s["pluto_uri"]:
        cfg.pluto_uri = s["pluto_uri"]

    if isinstance(s.get("permit_out_of_spec_frequency"), bool):
        cfg.permit_out_of_spec_frequency = s["permit_out_of_spec_frequency"]

    freq_range = cfg.freq_bounds

    def check_num(key: str, limits: Tuple[float, float], cast_type: type) -> None:
        val = s.get(key)
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

    if isinstance(s.get("auto_threshold"), bool):
        cfg.auto_threshold = s["auto_threshold"]

    return cfg


def load_settings() -> AppConfig:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return validate_config(json.load(f))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return AppConfig()


def save_settings(config: AppConfig) -> bool:
    """Write configuration to CONFIG_FILE atomically."""
    tmp_path = CONFIG_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=2)
        os.replace(tmp_path, CONFIG_FILE)
        return True
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False
