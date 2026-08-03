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

import datetime
import json
import math
import os
from typing import Optional

from pluto_siege.constants import (
    AUTHOR,
    BYTES_PER_SAMPLE,
    SAMPLE_RATE_RANGE,
    VERSION,
)
from pluto_siege.settings import brief, freq_bounds

# numpy is imported lazily via the callers; keep the module importable without it.
import numpy as np


def to_sigmf_utc(dt: datetime.datetime) -> str:
    return (dt.astimezone(datetime.timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def save_sigmf_pair(base_path: str, array: np.ndarray, freq: int, sr: int,
                    timestamp_iso: str, hw_model: str) -> None:
    """Write a SigMF data/meta pair, leaving no partial files behind on error.

    A truncated .sigmf-data would still replay as if it were valid, so both
    files are staged and renamed into place only once fully written.
    """
    tmp_data_path = base_path + ".sigmf-data.tmp"
    tmp_meta_path = base_path + ".sigmf-meta.tmp"
    data_path = base_path + ".sigmf-data"
    meta_path = base_path + ".sigmf-meta"
    promoted = False
    try:
        array.astype("<c8", copy=False).tofile(tmp_data_path)
        meta = {
            "global": {
                "core:datatype": "cf32_le", "core:sample_rate": int(sr),
                "core:hw": hw_model, "core:author": AUTHOR, "core:version": VERSION,
                "core:description": "Host-estimated timestamp. May have USB/IIO latency.",
            },
            "captures": [{
                "core:sample_start": 0, "core:frequency": int(freq),
                "core:datetime": timestamp_iso,
            }],
            "annotations": [],
        }
        with open(tmp_meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        os.replace(tmp_data_path, data_path)
        promoted = True
        os.replace(tmp_meta_path, meta_path)
    except BaseException:
        # BaseException, not Exception: SIGTERM arrives here as KeyboardInterrupt
        # (see sigterm_handler), and a data file promoted without its metadata is
        # exactly the half-written pair this staging exists to prevent.
        leftovers = [tmp_data_path, tmp_meta_path]
        if promoted:
            leftovers.append(data_path)
        for p in leftovers:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise


def load_sigmf_meta(path: str) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """Read a recording's own sample rate and frequency from its SigMF metadata.

    Returns (sample_rate, frequency, problem). On success problem is None; on any
    doubt at all it carries a one-line reason and both numbers are None, and the
    caller must refuse to transmit. Replaying an unknown file at whatever the
    current settings happen to be does not reproduce the signal: a wrong rate
    stretches or compresses the waveform and a wrong frequency puts the energy
    on someone else's band.
    """
    meta_path = path.removesuffix(".sigmf-data").removesuffix(".sigmf-meta") + ".sigmf-meta"
    if not os.path.exists(meta_path):
        return None, None, "no .sigmf-meta file beside it"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except json.JSONDecodeError:
        return None, None, "metadata is not valid JSON"
    except UnicodeDecodeError:
        return None, None, "metadata is not valid UTF-8"
    except OSError as e:
        return None, None, f"metadata unreadable ({brief(e.strerror or 'I/O error')})"
    try:
        if not isinstance(meta, dict):
            return None, None, "metadata is not a SigMF object"
        g = meta.get("global")
        if not isinstance(g, dict):
            return None, None, "metadata has no global block"
        dtype = g.get("core:datatype")
        if dtype is None:
            return None, None, "metadata has no sample format"
        if dtype != "cf32_le":
            return None, None, f"sample format is {brief(dtype, 12)!r}, not cf32_le"
        captures = meta.get("captures")
        first = captures[0] if isinstance(captures, list) and captures else None
        if not isinstance(first, dict):
            return None, None, "metadata has no captures block"
        sr, freq = g.get("core:sample_rate"), first.get("core:frequency")
        if isinstance(sr, bool) or isinstance(freq, bool) \
                or not isinstance(sr, (int, float)) or not isinstance(freq, (int, float)):
            return None, None, "metadata lacks a sample rate or a frequency"
        if not math.isfinite(sr) or not math.isfinite(freq):
            return None, None, "metadata rate or frequency is not finite"
        sr, freq = int(sr), int(freq)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as e:
        return None, None, f"metadata is malformed ({brief(e)})"
    srlo, srhi = SAMPLE_RATE_RANGE
    if not srlo <= sr <= srhi:
        return None, None, (f"rate {brief(sr, 9)} Hz outside "
                            f"{srlo / 1e3:g}k-{srhi / 1e6:g}M Hz")
    lo, hi = freq_bounds()
    if not lo <= freq <= hi:
        return None, None, (f"{brief(f'{freq / 1e6:.3f}', 11)} MHz outside "
                            f"{lo / 1e6:g}-{hi / 1e6:g} MHz")
    return sr, freq, None
