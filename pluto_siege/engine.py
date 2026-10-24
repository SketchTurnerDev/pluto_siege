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

from __future__ import annotations

import datetime
import math
import os
import time
from collections import deque
from dataclasses import replace
from typing import TYPE_CHECKING, Callable

import numpy as np
from numpy.typing import NDArray

from pluto_siege.config import CONFIG, AppConfig
from pluto_siege.constants import (
    AUTO_MARGIN_RANGE,
    BYTES_PER_SAMPLE,
    LO_TOLERANCE_HZ,
    LOOPBACK_TONE_HZ,
    LOOPBACK_TX_AMPLITUDE,
    MANUAL_RELEASE_DROP_DB,
    MAX_RAM_BYTES,
    MAX_TX_BURST_SAMPLES,
    MIN_TRIGGER_SUB_WINDOWS,
    NF_PROBE_BUFFERS,
    POST_TRIGGER_RANGE,
    PRE_TRIGGER_RANGE,
    RAM_SAFETY_FACTOR,
    RELEASE_MARGIN_DB,
    RX_FLUSH_BUFFERS,
    SILENCE_RANGE,
    SR_TOLERANCE_HZ,
    THRESHOLD_RANGE,
    TX_BACKOFF,
    TX_DAC_MAX,
    TX_DRAIN_MARGIN_SECONDS,
)
from pluto_siege.device import (
    SDRDevice,
    _check_number,
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
    noise_floor_dbfs,
    power_to_dbfs,
    subwindow_powers,
)
from pluto_siege.sigmf import load_sigmf_meta, save_sigmf_pair, to_sigmf_utc

if TYPE_CHECKING:
    from pluto_siege.gps import GPSFix


class CaptureEngine:
    """Receiver setup, noise estimation, trigger detection, and sample collection."""

    def __init__(self, sdr: SDRDevice, config: AppConfig | None = None) -> None:
        self.sdr = sdr
        self.config: AppConfig = CONFIG if config is None else config
        self.actual_sr = 0
        self.actual_freq = 0
        self.buf_size = 0
        self.noise_floor = -120.0
        self.trigger_threshold = 0.0
        self.release_threshold = 0.0
        self.io_timeout_available = True
        self._prepared = False
        self._capture_config: AppConfig | None = None
        self.captured_data: NDArray[np.complex64] | None = None
        self.start_time: datetime.datetime | None = None
        self._reset_result()

    def _reset_result(self) -> None:
        self.current_level_dbfs = -120.0
        self.current_saturated = False
        self.captured_data = None
        self.start_time = None
        self.is_partial = False
        self.is_aborted = False

    def _receive_buffer(self) -> NDArray[np.complex64]:
        data = safe_rx(self.sdr)
        if self.buf_size and data.size != self.buf_size:
            raise OSError("SDR buffer size changed during capture; recording stopped")
        return data

    def prepare(self) -> None:
        """Configure RX, snapshot settings, validate memory, and estimate thresholds."""
        self._prepared = False
        self._reset_result()
        self._capture_config = replace(self.config)
        cfg = self._capture_config
        _check_number(
            cfg.pre_trigger_buffers,
            PRE_TRIGGER_RANGE,
            "Pre-trigger buffers",
            integral=True,
        )
        _check_number(
            cfg.max_post_trigger_seconds, POST_TRIGGER_RANGE, "Post-trigger duration"
        )
        _check_number(cfg.silence_seconds, SILENCE_RANGE, "Silence duration")
        _check_number(cfg.manual_threshold, THRESHOLD_RANGE, "Manual threshold")
        _check_number(cfg.auto_trigger_margin, AUTO_MARGIN_RANGE, "Auto trigger margin")
        try:
            with suppress_c_stderr():
                self.io_timeout_available = cfg_rx(self.sdr, config=cfg)
                self.actual_sr = int(self.sdr.sample_rate)
                self.actual_freq = int(self.sdr.rx_lo)
                lo_min, lo_max = cfg.freq_bounds
                if (
                    self.actual_sr <= 0
                    or not (lo_min - LO_TOLERANCE_HZ <= self.actual_freq <= lo_max + LO_TOLERANCE_HZ)
                ):
                    raise OSError("Device reported invalid RX sample rate or frequency")
                for _ in range(RX_FLUSH_BUFFERS):
                    safe_rx(self.sdr)
                probe = safe_rx(self.sdr)
                self.buf_size = probe.size

                max_post_buffers = max(
                    1,
                    math.ceil(
                        cfg.max_post_trigger_seconds * self.actual_sr / self.buf_size
                    ),
                )
                total_buffers = cfg.pre_trigger_buffers + 1 + max_post_buffers
                est_ram = (
                    total_buffers * self.buf_size * BYTES_PER_SAMPLE * RAM_SAFETY_FACTOR
                )
                if est_ram > MAX_RAM_BYTES:
                    raise ValueError(
                        "Estimated RAM exceeds limit. Reduce SR or max_post_trigger_seconds."
                    )

                if cfg.auto_threshold:
                    nf_pool = [subwindow_powers(probe)]
                    for _ in range(NF_PROBE_BUFFERS - 1):
                        nf_pool.append(subwindow_powers(self._receive_buffer()))
                    self.noise_floor = noise_floor_dbfs(np.concatenate(nf_pool))
                    self.trigger_threshold = self.noise_floor + cfg.auto_trigger_margin
                    self.release_threshold = min(
                        self.noise_floor + RELEASE_MARGIN_DB,
                        self.trigger_threshold - 1.0,
                    )
                else:
                    self.noise_floor = -120.0
                    self.trigger_threshold = cfg.manual_threshold
                    self.release_threshold = (
                        self.trigger_threshold - MANUAL_RELEASE_DROP_DB
                    )
                self._prepared = True
        except BaseException:
            cleanup_sdr(self.sdr)
            raise

    def _collect_post_trigger_chunks(
        self,
        initial_data: NDArray[np.complex64],
        check_abort: Callable[[], bool],
        max_post_buffers: int,
        silence_buf_limit: int,
    ) -> list[NDArray[np.complex64]]:
        """Collect post-trigger buffers until silence, duration limit, or cancellation."""
        chunks = [initial_data]
        silence_count = 0
        post_buf_count = 0
        release_power = dbfs_to_power(self.release_threshold)
        while silence_count < silence_buf_limit and post_buf_count < max_post_buffers:
            if check_abort():
                self.is_aborted = True
                break
            data = self._receive_buffer()
            chunks.append(data)
            post_buf_count += 1
            silence_count = (
                silence_count + 1
                if float(np.max(subwindow_powers(data))) < release_power
                else 0
            )
        self.is_partial = (
            post_buf_count >= max_post_buffers and silence_count < silence_buf_limit
        )
        if silence_count > 1 and not self.is_partial and not self.is_aborted:
            chunks = chunks[: -(silence_count - 1)]
        return chunks

    def _finalize_captured_buffer(
        self,
        pre_trigger_data: list[NDArray[np.complex64]],
        chunks: list[NDArray[np.complex64]],
        prefix_samples: int,
        trigger_time: datetime.datetime,
    ) -> None:
        """Remove pre-trigger DC offset and assemble a timestamped buffer."""
        if not chunks:
            self.captured_data = np.empty(0, dtype=np.complex64)
            self.start_time = None
            return
        if any(is_saturated(chunk) for chunk in chunks):
            self.current_saturated = True
        valid_pre = [chunk for chunk in pre_trigger_data if chunk.size > 0]
        dc_offset = (
            np.complex64(np.concatenate(valid_pre).mean(dtype=np.complex128))
            if valid_pre
            else np.complex64(0)
        )
        captured = np.concatenate(chunks).astype(np.complex64, copy=False)
        captured -= dc_offset
        self.captured_data = captured
        self.start_time = trigger_time - datetime.timedelta(
            seconds=prefix_samples / self.actual_sr if self.actual_sr > 0 else 0.0
        )

    def listen_and_capture(
        self,
        check_abort: Callable[[], bool],
        on_trigger_detected: Callable[[], None] | None = None,
        on_meter_update: Callable[[float, bool], None] | None = None,
    ) -> None:
        """Listen using the prepared settings and capture a burst upon trigger."""
        if not self._prepared:
            raise RuntimeError(
                "CaptureEngine.prepare() must be called before listen_and_capture()"
            )
        self._reset_result()
        cfg = self._capture_config if self._capture_config is not None else self.config
        with suppress_c_stderr():
            ring: deque[NDArray[np.complex64]] = deque(maxlen=cfg.pre_trigger_buffers)
            for _ in range(cfg.pre_trigger_buffers):
                if check_abort():
                    self.is_aborted = True
                    return
                ring.append(self._receive_buffer())
            trigger_power = dbfs_to_power(self.trigger_threshold)
            max_post_buffers = max(
                1,
                math.ceil(
                    cfg.max_post_trigger_seconds * self.actual_sr / self.buf_size
                ),
            )
            silence_buf_limit = max(
                1, math.ceil(cfg.silence_seconds * self.actual_sr / self.buf_size)
            )
            while True:
                if check_abort():
                    self.is_aborted = True
                    return
                data = self._receive_buffer()
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
                    prefix_samples = (
                        sum(chunk.size for chunk in pre_trigger_data) + data.size
                    )
                    post_chunks = self._collect_post_trigger_chunks(
                        data, check_abort, max_post_buffers, silence_buf_limit
                    )
                    self._finalize_captured_buffer(
                        pre_trigger_data,
                        pre_trigger_data + post_chunks,
                        prefix_samples,
                        trigger_time,
                    )
                    break
                ring.append(data)

    def save_recording(
        self, records_dir: str, hw_model: str, gps_fix: GPSFix | None = None
    ) -> str:
        """Save captured buffer as a SigMF recording pair and return base path."""
        if (
            self.captured_data is None
            or self.captured_data.size == 0
            or self.start_time is None
        ):
            raise ValueError("No captured data to save")
        os.makedirs(records_dir, exist_ok=True)
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S_%f")
        base = os.path.join(
            records_dir, f"rec_{timestamp}_{self.actual_freq}_{self.actual_sr}"
        )
        save_sigmf_pair(
            base,
            self.captured_data,
            self.actual_freq,
            self.actual_sr,
            to_sigmf_utc(self.start_time),
            hw_model,
            gps_fix=gps_fix,
        )
        return base


class TransmitEngine:
    """Load a bounded recording and transmit one non-cyclic, drained burst."""

    @staticmethod
    def load_payload(path: str) -> NDArray[np.complex64]:
        clean_path = os.path.normpath(
            path.removesuffix(".sigmf-meta").removesuffix(".sigmf-data") + ".sigmf-data"
        )
        if not os.path.isfile(clean_path):
            raise ValueError("Target is not a regular file.")
        file_size = os.path.getsize(clean_path)
        if file_size == 0:
            raise ValueError("Recording is empty")
        if file_size % BYTES_PER_SAMPLE != 0:
            raise ValueError("File size not aligned to complex64")
        n_samples = file_size // BYTES_PER_SAMPLE
        if n_samples > MAX_TX_BURST_SAMPLES:
            raise ValueError(
                f"Recording too large: {n_samples} samples (limit {MAX_TX_BURST_SAMPLES})"
            )
        data = np.fromfile(clean_path, dtype="<c8", count=n_samples).astype(
            np.complex64, copy=False
        )
        if data.size != n_samples:
            raise ValueError("Incomplete read of recording")
        peak = float(np.max(np.abs(data.view(np.float32))))
        if not math.isfinite(peak):
            raise ValueError("NaN/Inf in recording")
        if peak < 1e-6:
            raise ValueError("Recording is too quiet or empty")
        data *= np.float32(TX_BACKOFF * TX_DAC_MAX / peak)
        return data

    @classmethod
    def prepare_transmission(
        cls, path: str, bounds: tuple[int, int] | None = None
    ) -> tuple[NDArray[np.complex64], int, int]:
        use_sr, use_freq, problem = load_sigmf_meta(path, bounds=bounds)
        if problem is not None or use_sr is None or use_freq is None:
            raise ValueError(f"untrusted recording - {problem}")
        return cls.load_payload(path), use_sr, use_freq

    @staticmethod
    def transmit(
        sdr: SDRDevice,
        data: NDArray[np.complex64],
        sample_rate: int,
        freq: int,
        config: AppConfig | None = None,
    ) -> bool:
        try:
            if (
                not isinstance(data, np.ndarray)
                or data.ndim != 1
                or not np.iscomplexobj(data)
            ):
                raise ValueError("TX payload must be a one-dimensional complex array")
            if not 0 < data.size <= MAX_TX_BURST_SAMPLES:
                raise ValueError("TX payload is empty or exceeds the sample limit")
            if not bool(np.all(np.isfinite(data))):
                raise ValueError("TX payload contains non-finite samples")
            peak = max(
                float(np.max(np.abs(data.real))), float(np.max(np.abs(data.imag)))
            )
            if peak > TX_DAC_MAX:
                raise ValueError("TX payload exceeds the DAC amplitude limit")
            payload = np.ascontiguousarray(data, dtype=np.complex64)
            cfg_tx(sdr, sample_rate, freq, config=config)
            actual_sr = int(sdr.sample_rate)
            actual_freq = int(sdr.tx_lo)
            if (
                actual_sr <= 0
                or abs(actual_sr - sample_rate) > SR_TOLERANCE_HZ
                or abs(actual_freq - freq) > LO_TOLERANCE_HZ
            ):
                raise OSError(
                    "SDR did not apply the recording's sample rate or frequency"
                )
            duration = payload.size / actual_sr
            timeout_ok = set_io_timeout(sdr, duration)
            with suppress_c_stderr():
                sdr.tx(payload)
            deadline = time.monotonic() + duration + TX_DRAIN_MARGIN_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.05))
            return timeout_ok
        finally:
            cleanup_sdr(sdr)


class LoopbackTester:
    """Digital loopback tone generator and SNR verifier."""

    @staticmethod
    def run_test(
        sdr: SDRDevice,
        sample_rate: int,
        rx_buffer_size: int,
        config: AppConfig | None = None,
    ) -> tuple[float, float, bool]:
        cfg = replace(
            CONFIG if config is None else config,
            sample_rate=sample_rate,
            rx_buffer_size=rx_buffer_size,
        )
        try:
            timeout_ok = cfg_loopback(sdr, config=cfg)
            fs = int(sdr.sample_rate)
            if fs <= 0 or rx_buffer_size <= 0:
                raise OSError("Invalid sample rate or buffer size reported by SDR")
            n_samples = int(rx_buffer_size)
            tone_bin = max(1, round(LOOPBACK_TONE_HZ * n_samples / fs))
            tone_freq = tone_bin * fs / n_samples
            sample_indices = np.arange(n_samples, dtype=np.float64)
            tx = (
                LOOPBACK_TX_AMPLITUDE
                * np.exp(2j * np.pi * tone_bin * sample_indices / n_samples)
                * TX_DAC_MAX
            ).astype(np.complex64)
            with suppress_c_stderr():
                sdr.tx(tx)
                time.sleep(0.05)
                for _ in range(2):
                    safe_rx(sdr)
                rx = safe_rx(sdr)
            return calculate_snr_db(rx, fs, tone_freq), tone_freq, timeout_ok
        finally:
            cleanup_sdr(sdr)
