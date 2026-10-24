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

"""Signal-level metrics: sub-window power, dBFS conversions, saturation, and SNR."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from pluto_siege.constants import (
    DB_EPSILON,
    DETECT_SUB_WINDOW,
    LOOPBACK_TONE_BINS,
    NF_PERCENTILE,
    RX_FULL_SCALE,
    SATURATION_MARGIN,
)

INV_FULL_SCALE_SQ = 1.0 / (RX_FULL_SCALE * RX_FULL_SCALE)


def subwindow_powers(
    data: NDArray[np.complex64], sub_size: int = DETECT_SUB_WINDOW
) -> NDArray[np.float64]:
    """Full-scale-normalised mean powers, including a final partial sub-window."""
    sub_size = max(1, int(sub_size))
    if data.ndim != 1:
        data = data.ravel()
    if data.dtype != np.complex64 or not data.flags["C_CONTIGUOUS"]:
        data = np.ascontiguousarray(data, dtype=np.complex64)
    n_samples = data.size
    if n_samples == 0:
        return np.zeros(1, dtype=np.float64)
    usable = n_samples - n_samples % sub_size
    if usable:
        flat = data[:usable].view(np.float32).reshape(-1, sub_size * 2)
        powers = np.einsum("ij,ij->i", flat, flat, dtype=np.float64) * (
            INV_FULL_SCALE_SQ / sub_size
        )
    else:
        powers = np.empty(0, dtype=np.float64)
    if usable < n_samples:
        tail = data[usable:].view(np.float32)
        power = float(np.einsum("i,i->", tail, tail, dtype=np.float64))
        powers = np.append(powers, power * INV_FULL_SCALE_SQ / (n_samples - usable))
    return powers


def power_to_dbfs(power: float) -> float:
    """Linear full-scale-normalised power to dBFS; unusable inputs give the floor."""
    if not math.isfinite(power) or power <= 0.0:
        return -120.0
    return float(10.0 * math.log10(power + DB_EPSILON))


def dbfs_to_power(dbfs: float) -> float:
    """Convert a threshold without log work per buffer, bounded against overflow."""
    if math.isnan(dbfs):
        return 0.0
    return float(10.0 ** (min(dbfs, 300.0) / 10.0))


def max_subwindow_dbfs(
    data: NDArray[np.complex64], sub_size: int = DETECT_SUB_WINDOW
) -> float:
    """Peak sub-window level in dBFS, used for triggering."""
    return power_to_dbfs(float(np.max(subwindow_powers(data, sub_size))))


peak_window_dbfs = max_subwindow_dbfs


def noise_floor_dbfs(
    powers: NDArray[np.float64], percentile: float = NF_PERCENTILE
) -> float:
    """Noise floor from pooled finite sub-window powers."""
    if powers.size == 0:
        return -120.0
    valid = powers[np.isfinite(powers)]
    if valid.size == 0:
        return -120.0
    return power_to_dbfs(float(np.percentile(valid, percentile)))


def is_saturated(
    data: NDArray[np.complex64], margin: float = SATURATION_MARGIN
) -> bool:
    if data.size == 0:
        return False
    if data.ndim != 1:
        data = data.ravel()
    limit = margin * RX_FULL_SCALE
    if data.dtype != np.complex64 or not data.flags["C_CONTIGUOUS"]:
        data = np.ascontiguousarray(data, dtype=np.complex64)
    flat = data.view(np.float32)
    return bool(max(float(np.max(flat)), -float(np.min(flat))) >= limit)


def calculate_snr_db(
    rx: NDArray[np.complex64],
    fs: float,
    tone_freq: float,
    tone_bins: int = LOOPBACK_TONE_BINS,
) -> float:
    """Estimate in-band SNR from a Hann-windowed FFT, wrapping at Nyquist.

    Noise per bin is estimated outside the tone band, then scaled to the band
    width. This preserves the existing local-band metric (not broadband SNR).
    """
    if rx.ndim != 1:
        rx = rx.ravel()
    n_samples = rx.size
    if (
        n_samples < 32
        or not math.isfinite(fs)
        or fs <= 0
        or not math.isfinite(tone_freq)
        or not -fs / 2 <= tone_freq < fs / 2
        or not bool(np.all(np.isfinite(rx)))
    ):
        return -120.0
    tone_bins = max(1, int(tone_bins))
    windowed_fft = np.fft.fft(rx * np.hanning(n_samples))
    spectrum = windowed_fft.real**2 + windowed_fft.imag**2
    if float(np.sum(spectrum)) <= 1e-18:
        return -120.0
    if 2 * tone_bins + 1 >= n_samples:
        return 0.0

    target_bin = round(tone_freq * n_samples / fs) % n_samples
    signal_bins = (target_bin + np.arange(-tone_bins, tone_bins + 1)) % n_samples
    in_band_mask = np.zeros(n_samples, dtype=bool)
    in_band_mask[signal_bins] = True
    in_band = float(np.sum(spectrum[in_band_mask]))
    if in_band <= 1e-18:
        return -120.0
    # Sum the noise bins directly. Subtracting two nearly equal large totals
    # loses the residual noise on a clean loopback tone.
    noise = float(np.mean(spectrum[~in_band_mask])) * signal_bins.size
    signal_power = max(in_band - noise, 1e-20)
    return float(10.0 * math.log10(signal_power / max(noise, 1e-20)))
