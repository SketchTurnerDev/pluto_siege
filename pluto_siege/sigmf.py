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

"""SigMF recording save and load utilities."""

from __future__ import annotations

import datetime
import json
import math
import os
import tempfile
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from pluto_siege.constants import (
    AUTHOR,
    FREQ_RANGE_EXTENDED,
    FREQ_RANGE_IN_SPEC,
    RX_FULL_SCALE,
    SAMPLE_RATE_RANGE,
    VERSION,
    brief,
)

if TYPE_CHECKING:
    from pluto_siege.gps import GPSFix

_MAX_METADATA_BYTES = 8 * 1024 * 1024


def to_sigmf_utc(dt: datetime.datetime) -> str:
    """Format a datetime as an ISO-8601 UTC string with microsecond precision."""
    return (
        dt.astimezone(datetime.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _meta_path(path: str) -> str:
    return path.removesuffix(".sigmf-data").removesuffix(".sigmf-meta") + ".sigmf-meta"


def _finite_int(value: object) -> int | None:
    """Return an integer when value is a finite whole number, otherwise None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        num = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(num) or not num.is_integer():
        return None
    return int(num)


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        num = float(value)
    except (OverflowError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _validate_coords(
    lon: object, lat: object, alt: object = 0.0
) -> tuple[float, float, float] | None:
    """Validate geographic coordinates against RFC 7946 bounds."""
    lon_f = _finite_float(lon)
    lat_f = _finite_float(lat)
    alt_f = _finite_float(alt)
    if lon_f is None or lat_f is None or alt_f is None:
        return None
    if not (-180.0 <= lon_f <= 180.0 and -90.0 <= lat_f <= 90.0):
        return None
    return lon_f, lat_f, alt_f


def _supported_capture(
    meta: object,
) -> tuple[tuple[dict[str, Any], dict[str, Any]] | None, str | None]:
    """Validate global and first capture blocks for scalar cf32_le playback."""
    if not isinstance(meta, dict):
        return None, "metadata is not a SigMF object"
    global_meta = meta.get("global")
    if not isinstance(global_meta, dict):
        return None, "metadata has no global block"
    dtype = global_meta.get("core:datatype")
    if dtype is None:
        return None, "metadata has no sample format"
    if dtype != "cf32_le":
        return None, f"sample format is {brief(dtype, 12)!r}, not cf32_le"

    captures = meta.get("captures")
    if not isinstance(captures, list) or not captures:
        return None, "metadata has no captures block"
    capture = captures[0]
    if (
        not isinstance(capture, dict)
        or _finite_int(capture.get("core:sample_start", 0)) != 0
    ):
        return None, "metadata must start capture at sample 0"

    if (
        _finite_int(global_meta.get("core:num_channels", 1)) != 1
        or _finite_int(global_meta.get("core:offset", 0)) != 0
        or _finite_int(global_meta.get("core:trailing_bytes", 0)) != 0
        or "core:dataset" in global_meta
        or _finite_int(capture.get("core:header_bytes", 0)) != 0
    ):
        return None, "metadata uses an unsupported data layout"

    return (global_meta, capture), None


def save_sigmf_pair(
    base_path: str,
    array: NDArray[np.complex64],
    freq: int,
    sr: int,
    timestamp_iso: str,
    hw_model: str,
    gps_fix: GPSFix | None = None,
) -> None:
    """Save captured IQ samples and metadata atomically as a SigMF pair."""
    if not isinstance(base_path, str) or not base_path:
        raise ValueError("base_path must be a non-empty string")
    if (
        not isinstance(array, np.ndarray)
        or array.ndim != 1
        or not np.iscomplexobj(array)
    ):
        raise ValueError("SigMF samples must be a one-dimensional complex array")
    if not bool(np.all(np.isfinite(array))):
        raise ValueError("SigMF samples must be finite")

    use_freq = _finite_int(freq)
    use_sr = _finite_int(sr)
    if (
        use_freq is None
        or not FREQ_RANGE_EXTENDED[0] <= use_freq <= FREQ_RANGE_EXTENDED[1]
    ):
        raise ValueError("frequency is outside the supported tuning range")
    if use_sr is None or not SAMPLE_RATE_RANGE[0] <= use_sr <= SAMPLE_RATE_RANGE[1]:
        raise ValueError("sample rate is outside the supported range")
    if not isinstance(timestamp_iso, str) or not timestamp_iso:
        raise ValueError("timestamp must be a non-empty string")
    if not isinstance(hw_model, str):
        raise TypeError("hardware model must be a string")

    data_path = base_path + ".sigmf-data"
    meta_path = base_path + ".sigmf-meta"
    directory = os.path.dirname(os.path.abspath(base_path))
    os.makedirs(directory, exist_ok=True)

    tmp_data_path: str | None = None
    tmp_meta_path: str | None = None
    try:
        norm_data = (array * np.float32(1.0 / RX_FULL_SCALE)).astype("<c8", copy=False)
        data_fd, tmp_data_path = tempfile.mkstemp(
            prefix=".sigmf-data-", suffix=".tmp", dir=directory
        )
        with os.fdopen(data_fd, "wb") as data_file:
            norm_data.tofile(data_file)
            data_file.flush()
            os.fsync(data_file.fileno())

        capture_entry: dict[str, Any] = {
            "core:sample_start": 0,
            "core:frequency": use_freq,
            "core:datetime": timestamp_iso,
        }
        if gps_fix is not None and getattr(gps_fix, "has_fix", False) is True:
            coords = _validate_coords(
                getattr(gps_fix, "longitude", None),
                getattr(gps_fix, "latitude", None),
                getattr(gps_fix, "altitude", 0.0),
            )
            if coords is not None:
                lon, lat, alt = coords
                capture_entry["core:geolocation"] = {
                    "type": "Point",
                    "coordinates": [round(lon, 6), round(lat, 6), round(alt, 1)],
                }

        meta = {
            "global": {
                "core:datatype": "cf32_le",
                "core:sample_rate": use_sr,
                "core:hw": hw_model,
                "core:author": AUTHOR,
                "core:version": "1.0.0",
                "core:recorder": f"PlutoSiege v{VERSION}",
                "core:description": "Host-estimated timestamp. May have USB/IIO latency.",
            },
            "captures": [capture_entry],
            "annotations": [],
        }
        meta_fd, tmp_meta_path = tempfile.mkstemp(
            prefix=".sigmf-meta-", suffix=".tmp", dir=directory
        )
        with os.fdopen(meta_fd, "w", encoding="utf-8") as meta_file:
            json.dump(meta, meta_file, indent=2, allow_nan=False)
            meta_file.flush()
            os.fsync(meta_file.fileno())

        os.replace(tmp_data_path, data_path)
        tmp_data_path = None
        try:
            os.replace(tmp_meta_path, meta_path)
            tmp_meta_path = None
        except BaseException:
            if os.path.exists(data_path):
                try:
                    os.unlink(data_path)
                except OSError:
                    pass
            raise
    finally:
        for tmp_file in (tmp_data_path, tmp_meta_path):
            if tmp_file is not None and os.path.exists(tmp_file):
                try:
                    os.unlink(tmp_file)
                except OSError:
                    pass


def _load_sigmf_json(meta_path: str) -> tuple[object | None, str | None]:
    if not os.path.exists(meta_path):
        return None, "no .sigmf-meta file beside it"
    try:
        if os.path.getsize(meta_path) > _MAX_METADATA_BYTES:
            return None, "metadata unreadable (metadata exceeds 8 MiB)"
        with open(meta_path, "rb") as f:
            raw_bytes = f.read()
    except FileNotFoundError:
        return None, "no .sigmf-meta file beside it"
    except OSError as error:
        return None, f"metadata unreadable ({brief(error.strerror or 'I/O error')})"

    try:
        return json.loads(raw_bytes.decode("utf-8")), None
    except UnicodeDecodeError:
        return None, "metadata is not valid UTF-8"
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None, "metadata is not valid JSON"


def load_sigmf_meta(
    path: str, bounds: tuple[int, int] | None = None
) -> tuple[int | None, int | None, str | None]:
    """Read and validate sample rate and center frequency from a SigMF recording."""
    meta, problem = _load_sigmf_json(_meta_path(path))
    if problem is not None:
        return None, None, problem

    parsed, err = _supported_capture(meta)
    if parsed is None:
        return None, None, err
    global_meta, capture = parsed

    sr = _finite_int(global_meta.get("core:sample_rate"))
    freq = _finite_int(capture.get("core:frequency"))
    if sr is None or freq is None:
        return None, None, "metadata lacks an integral finite sample rate or frequency"

    srlo, srhi = SAMPLE_RATE_RANGE
    if not srlo <= sr <= srhi:
        return (
            None,
            None,
            f"rate {brief(sr, 9)} Hz outside {srlo / 1e3:g}k-{srhi / 1e6:g}M Hz",
        )

    if bounds is None:
        low, high = FREQ_RANGE_IN_SPEC
    else:
        if not isinstance(bounds, tuple) or len(bounds) != 2:
            return None, None, "invalid frequency bounds"
        b_lo, b_hi = _finite_int(bounds[0]), _finite_int(bounds[1])
        if b_lo is None or b_hi is None or b_lo > b_hi:
            return None, None, "invalid frequency bounds"
        low, high = b_lo, b_hi

    if not low <= freq <= high:
        freq_str = f"{freq / 1e6:.3f}"
        return (
            None,
            None,
            f"{brief(freq_str, 11)} MHz outside {low / 1e6:g}-{high / 1e6:g} MHz",
        )
    return sr, freq, None


def load_sigmf_geolocation(path: str) -> tuple[float, float, float] | None:
    """Extract (longitude, latitude, altitude) from a SigMF capture if present and valid."""
    meta, problem = _load_sigmf_json(_meta_path(path))
    if problem is not None:
        return None
    parsed, _ = _supported_capture(meta)
    if parsed is None:
        return None
    _, capture = parsed
    geo = capture.get("core:geolocation")
    if not isinstance(geo, dict) or geo.get("type") != "Point":
        return None
    coords = geo.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        return None
    alt = coords[2] if len(coords) >= 3 else 0.0
    return _validate_coords(coords[0], coords[1], alt)
