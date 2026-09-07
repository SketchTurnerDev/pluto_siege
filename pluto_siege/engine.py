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

"""Engines for RF signal capture, transmission, and loopback testing."""

from collections import deque
import datetime
import math
import os
import stat
import time
from typing import Callable, List, Optional, Tuple

import numpy as np

from pluto_siege.constants import (
    BYTES_PER_SAMPLE,
    LOOPBACK_TONE_HZ,
    LOOPBACK_TX_AMPLITUDE,
    MANUAL_RELEASE_DROP_DB,
    MAX_RAM_BYTES,
    MAX_TX_BURST_SAMPLES,
    MIN_TRIGGER_SUB_WINDOWS,
    NF_PROBE_BUFFERS,
    RAM_SAFETY_FACTOR,
    RELEASE_MARGIN_DB,
    RX_FLUSH_BUFFERS,
    TRIGGER_MARGIN_DB,
    TX_BACKOFF,
    TX_DAC_MAX,
    TX_DRAIN_MARGIN_SECONDS,
)
from pluto_siege.device import (
    SDRDevice,
    cfg_loopback,
    cfg_rx,
    cfg_tx,
    cleanup_sdr,
    safe_rx,
    set_io_timeout,
    suppress_c_stderr,
)
from pluto_siege.dsp import (
    calculate_snr_db,
    dbfs_to_power,
    is_saturated,
    max_subwindow_dbfs,
    noise_floor_dbfs,
    power_to_dbfs,
    subwindow_powers,
)
from pluto_siege.settings import CONFIG
from pluto_siege.sigmf import load_sigmf_meta, save_sigmf_pair, to_sigmf_utc


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

    def save_recording(self, records_dir: str, hw_model: str) -> str:
        if self.captured_data is None or self.start_time is None:
            raise ValueError("No captured data to save")
        os.makedirs(records_dir, exist_ok=True)
        ts = self.start_time.strftime("%Y%m%d_%H%M%S_%f")
        base = os.path.join(
            records_dir, f"rec_{ts}_{self.actual_freq}_{self.actual_sr}"
        )
        save_sigmf_pair(
            base,
            self.captured_data,
            self.actual_freq,
            self.actual_sr,
            to_sigmf_utc(self.start_time),
            hw_model,
        )
        return base


class TransmitEngine:
    @staticmethod
    def load_payload(path: str) -> np.ndarray:
        st = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("Target is not a regular file.")
        if st.st_size == 0:
            raise ValueError("Recording is empty")
        if st.st_size % BYTES_PER_SAMPLE != 0:
            raise ValueError("File size not aligned to complex64")
        n_samples = st.st_size // BYTES_PER_SAMPLE
        if n_samples > MAX_TX_BURST_SAMPLES:
            raise ValueError(f"Recording too large: {n_samples} samples (limit {MAX_TX_BURST_SAMPLES})")

        data = np.fromfile(path, dtype="<c8", count=n_samples)
        if data.size != n_samples:
            raise ValueError("Recording shrank while being read")
        data = np.asarray(data, dtype=np.complex64)

        flat = data.view(np.float32)
        hi, lo = float(flat.max()), float(flat.min())
        if not (math.isfinite(hi) and math.isfinite(lo)):
            raise ValueError("NaN/Inf in recording")
        peak = max(hi, -lo)
        if peak < 1e-6:
            raise ValueError("Recording is too quiet or empty")
        data *= np.float32(TX_BACKOFF * TX_DAC_MAX / peak)
        return data

    @classmethod
    def prepare_transmission(cls, path: str) -> Tuple[np.ndarray, int, int]:
        use_sr, use_freq, problem = load_sigmf_meta(path)
        if problem is not None or use_sr is None or use_freq is None:
            raise ValueError(f"untrusted recording - {problem}")
        data = cls.load_payload(path)
        return data, use_sr, use_freq

    @staticmethod
    def transmit(sdr: SDRDevice, data: np.ndarray, sample_rate: int, freq: int) -> bool:
        cfg_tx(sdr, sample_rate, freq)
        actual_sr = int(sdr.sample_rate)
        timeout_ok = set_io_timeout(sdr, data.size / actual_sr)
        try:
            on_air_end = time.monotonic() + data.size / actual_sr
            with suppress_c_stderr():
                sdr.tx(data)
            drain = on_air_end + TX_DRAIN_MARGIN_SECONDS - time.monotonic()
            if drain > 0:
                time.sleep(drain)
        finally:
            cleanup_sdr(sdr)
        return timeout_ok


class LoopbackTester:
    @staticmethod
    def run_test(sdr: SDRDevice, sample_rate: int, rx_buffer_size: int) -> Tuple[float, float, bool]:
        try:
            timeout_ok = cfg_loopback(sdr)
            fs = int(sdr.sample_rate)
            n_samples = int(rx_buffer_size)
            tone_bin = max(1, round(LOOPBACK_TONE_HZ * n_samples / fs))
            tone_freq = tone_bin * fs / n_samples

            n = np.arange(n_samples, dtype=np.float64)
            tx = (
                LOOPBACK_TX_AMPLITUDE
                * np.exp(2j * np.pi * tone_bin * n / n_samples)
                * TX_DAC_MAX
            ).astype(np.complex64)

            with suppress_c_stderr():
                sdr.tx(tx)
            time.sleep(0.05)
            for _ in range(2):
                safe_rx(sdr)
            rx = safe_rx(sdr)

            snr_db = calculate_snr_db(rx, fs, tone_freq)
            return snr_db, tone_freq, timeout_ok
        finally:
            cleanup_sdr(sdr)
