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

"""Settings module re-exporting AppConfig instance for compatibility."""

from typing import Any, Optional, Tuple

from pluto_siege.config import (
    AppConfig,
    load_settings,
    save_settings as _save_config,
    validate_config,
)

DEFAULT_SETTINGS = AppConfig().to_dict()
CONFIG = load_settings()

# Backwards compatibility aliases
SETTINGS = CONFIG


def save_settings(cfg: Optional[AppConfig] = None) -> bool:
    return _save_config(CONFIG if cfg is None else cfg)


def validate_settings(s: Any) -> dict:
    if isinstance(s, AppConfig):
        raw = s.to_dict()
    elif isinstance(s, dict):
        raw = s
    else:
        raw = {}
    return validate_config(raw).to_dict()


def brief(text: Any, limit: int = 18) -> str:
    """Shorten one variable fragment so the line it lands in still fits the screen."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "~"


def freq_bounds(cfg: Optional[AppConfig] = None) -> Tuple[int, int]:
    return (CONFIG if cfg is None else cfg).freq_bounds
