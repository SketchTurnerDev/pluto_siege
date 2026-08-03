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

"""Capture engine for RF signal detection, trigger evaluation, and memory-safe sample recording."""

from collections import deque
import datetime
import math
from typing import Callable, List, Optional, Tuple

import numpy as np

from pluto_siege.constants import (
    BYTES_PER_SAMPLE,
    MANUAL_RELEASE_DROP_DB,
    MAX_RAM_BYTES,
    MIN_TRIGGER_SUB_WINDOWS,
    NF_PROBE_BUFFERS,
    RAM_SAFETY_FACTOR,
    RELEASE_MARGIN_DB,
    RX_FLUSH_BUFFERS,
    TRIGGER_MARGIN_DB,
)
from pluto_siege.device import SDRDevice, cfg_rx, safe_rx
from pluto_siege.dsp import (
    dbfs_to_power,
    is_saturated,
    max_subwindow_dbfs,
    noise_floor_dbfs,
    power_to_dbfs,
    subwindow_powers,
)
from pluto_siege.settings import CONFIG


class CaptureEngine:
    """Encapsulates receiver setup, noise estimation, trigger detection, and sample collection."""

    def __init__(self, sdr: SDRDevice):
        self.sdr = sdr
        self.actual_sr: int = 0
        self.actual_freq: int = 0
        self.buf_size: int = 0
        self.trigger_threshold: float = 0.0
        self.release_threshold: float = 0.0
        self.noise_floor: float = 0.0
        self.io_timeout_available: bool = True

        # State outputs for UI / callers
        self.current_level_dbfs: float = -120.0
        self.current_saturated: bool = False
        self.captured_data: Optional[np.ndarray] = None
        self.start_time: Optional[datetime.datetime] = None
        self.is_partial: bool = False
        self.is_aborted: bool = False

    def prepare(self) -> None:
        """Configure SDR RX, validate RAM budget, and estimate noise floor/thresholds."""
        self.io_timeout_available = cfg_rx(self.sdr)
        self.actual_sr = int(self.sdr.sample_rate)
        self.actual_freq = int(self.sdr.rx_lo)

        for _ in range(RX_FLUSH_BUFFERS):
            safe_rx(self.sdr)

        probe = safe_rx(self.sdr)
        self.buf_size = probe.size

        max_post_buffers = max(
            1,
            math.ceil(
                (CONFIG.max_post_trigger_seconds * self.actual_sr) / self.buf_size
            ),
        )
        total_buffers = CONFIG.pre_trigger_buffers + 1 + max_post_buffers
        est_ram = total_buffers * self.buf_size * BYTES_PER_SAMPLE * RAM_SAFETY_FACTOR
        if est_ram > MAX_RAM_BYTES:
            raise ValueError(
                "Estimated RAM exceeds limit. "
                "Reduce SR or max_post_trigger_seconds."
            )

        if CONFIG.auto_threshold:
            nf_pool = [subwindow_powers(probe)]
            for _ in range(NF_PROBE_BUFFERS - 1):
                nf_pool.append(subwindow_powers(safe_rx(self.sdr)))
            self.noise_floor = noise_floor_dbfs(np.concatenate(nf_pool))
            self.trigger_threshold = self.noise_floor + float(CONFIG.auto_trigger_margin)
            self.release_threshold = self.noise_floor + RELEASE_MARGIN_DB
        else:
            self.trigger_threshold = float(CONFIG.manual_threshold)
            self.release_threshold = self.trigger_threshold - MANUAL_RELEASE_DROP_DB

    def _collect_post_trigger_chunks(
        self,
        initial_data: np.ndarray,
        check_abort: Callable[[], bool],
        max_post_buffers: int,
        silence_buf_limit: int,
    ) -> List[np.ndarray]:
        """Collect post-trigger sample buffers until silence or timeout."""
        chunks = [initial_data]
        silence_count = 0
        post_buf_count = 0
        while silence_count < silence_buf_limit and post_buf_count < max_post_buffers:
            if check_abort():
                self.is_partial = True
                break
            d = safe_rx(self.sdr)
            chunks.append(d)
            post_buf_count += 1
            silence_count = (
                silence_count + 1
                if max_subwindow_dbfs(d) < self.release_threshold
                else 0
            )
        return chunks

    def _finalize_captured_buffer(
        self,
        pre_trigger_data: List[np.ndarray],
        chunks: List[np.ndarray],
        prefix_samples: int,
        trigger_time: datetime.datetime,
    ) -> None:
        """Subtract DC offset and concatenate chunks into pre-allocated output array."""
        if any(is_saturated(chunk) for chunk in chunks):
            self.current_saturated = True

        dc_offset = np.complex64(
            np.mean([chunk.mean(dtype=np.complex128) for chunk in pre_trigger_data])
        )

        total_samples = sum(c.size for c in chunks)
        captured = np.empty(total_samples, dtype=np.complex64)
        offset = 0
        for chunk in chunks:
            c_len = chunk.size
            captured[offset : offset + c_len] = chunk
            offset += c_len

        captured -= dc_offset
        self.captured_data = captured
        self.start_time = trigger_time - datetime.timedelta(
            seconds=prefix_samples / self.actual_sr
        )

    def listen_and_capture(
        self,
        check_abort: Callable[[], bool],
        on_trigger_detected: Optional[Callable[[], None]] = None,
        on_meter_update: Optional[Callable[[float, bool], None]] = None,
    ) -> None:
        """Main listening loop."""
        ring: deque = deque(maxlen=CONFIG.pre_trigger_buffers)
        for _ in range(CONFIG.pre_trigger_buffers):
            ring.append(safe_rx(self.sdr))

        trigger_power = dbfs_to_power(self.trigger_threshold)
        max_post_buffers = max(
            1,
            math.ceil(
                (CONFIG.max_post_trigger_seconds * self.actual_sr) / self.buf_size
            ),
        )
        silence_buf_limit = max(
            1,
            math.ceil((CONFIG.silence_seconds * self.actual_sr) / self.buf_size),
        )

        while True:
            if check_abort():
                self.is_aborted = True
                return

            data = safe_rx(self.sdr)
            powers = subwindow_powers(data)
            self.current_level_dbfs = power_to_dbfs(float(powers.max()))
            self.current_saturated = is_saturated(data)
            hot_windows = int(np.count_nonzero(powers > trigger_power))

            if on_meter_update:
                on_meter_update(self.current_level_dbfs, self.current_saturated)

            if hot_windows >= MIN_TRIGGER_SUB_WINDOWS:
                trigger_time = datetime.datetime.now(datetime.timezone.utc)
                if on_trigger_detected:
                    on_trigger_detected()

                pre_trigger_data = list(ring)
                prefix_samples = sum(x.size for x in pre_trigger_data) + data.size

                post_chunks = self._collect_post_trigger_chunks(
                    data, check_abort, max_post_buffers, silence_buf_limit
                )
                all_chunks = pre_trigger_data + post_chunks

                self._finalize_captured_buffer(
                    pre_trigger_data, all_chunks, prefix_samples, trigger_time
                )
                break

            ring.append(data)
