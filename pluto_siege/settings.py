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

"""Settings re-exports from pluto_siege.config."""

from __future__ import annotations

from typing import Any

from pluto_siege.config import (
    CONFIG,
    DEFAULT_SETTINGS,
    AppConfig,
    freq_bounds,
    load_settings,
    save_settings,
    validate_config,
)


def validate_settings(s: Any) -> dict[str, Any]:
    """Validate a raw settings dict or AppConfig and return a clean dictionary."""
    if isinstance(s, AppConfig):
        raw = s.to_dict()
    elif isinstance(s, dict):
        raw = s
    else:
        raw = {}
    return validate_config(raw).to_dict()


__all__ = [
    "CONFIG",
    "DEFAULT_SETTINGS",
    "AppConfig",
    "freq_bounds",
    "load_settings",
    "save_settings",
    "validate_config",
    "validate_settings",
]
