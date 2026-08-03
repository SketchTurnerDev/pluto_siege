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

import math

import numpy as np

from pluto_siege.constants import (
    DB_EPSILON,
    DETECT_SUB_WINDOW,
    LOOPBACK_TONE_BINS,
    NF_PERCENTILE,
    RX_FULL_SCALE,
    SATURATION_MARGIN,
)


def subwindow_powers(data: np.ndarray, sub_size: int = DETECT_SUB_WINDOW) -> np.ndarray:
    """Mean power of each consecutive sub-window, normalised to full scale (linear)."""
    if data.dtype != np.complex64 or not data.flags["C_CONTIGUOUS"]:
        data = np.ascontiguousarray(data, dtype=np.complex64)
    n = data.size
    if n == 0:
        return np.zeros(1, dtype=np.float64)
    inv = 1.0 / (RX_FULL_SCALE * RX_FULL_SCALE)
    if n < sub_size:
        flat = data.view(np.float32)
        total = float(np.einsum("i,i->", flat, flat, dtype=np.float64))
        return np.array([total * inv / n], dtype=np.float64)
    usable = n - n % sub_size
    flat = data[:usable].view(np.float32).reshape(-1, sub_size * 2)
    sums = np.einsum("ij,ij->i", flat, flat, dtype=np.float64)
    return sums * (inv / sub_size)


def power_to_dbfs(power: float) -> float:
    """Linear full-scale-normalised power to dBFS, finite even at zero power."""
    return float(10.0 * math.log10(power + DB_EPSILON))


def dbfs_to_power(dbfs: float) -> float:
    """Inverse of power_to_dbfs(), for comparing thresholds without a log per buffer."""
    return float(10.0 ** (dbfs / 10.0))


def max_subwindow_dbfs(data: np.ndarray, sub_size: int = DETECT_SUB_WINDOW) -> float:
    """Peak sub-window level, used for triggering."""
    return power_to_dbfs(float(np.max(subwindow_powers(data, sub_size))))


def noise_floor_dbfs(powers: np.ndarray, percentile: float = NF_PERCENTILE) -> float:
    """Noise floor from pooled sub-window powers."""
    return power_to_dbfs(float(np.percentile(powers, percentile)))


def is_saturated(data: np.ndarray, margin: float = SATURATION_MARGIN) -> bool:
    if data.size == 0:
        return False
    limit = margin * RX_FULL_SCALE
    if data.dtype != np.complex64 or not data.flags["C_CONTIGUOUS"]:
        data = np.ascontiguousarray(data, dtype=np.complex64)
    flat = data.view(np.float32)
    return bool(max(float(flat.max()), -float(flat.min())) >= limit)


def calculate_snr_db(rx: np.ndarray, fs: float, tone_freq: float,
                     tone_bins: int = LOOPBACK_TONE_BINS) -> float:
    """Calculate Signal-to-Noise Ratio (SNR) in dB from Hanning-windowed FFT spectrum."""
    rx_norm = rx / RX_FULL_SCALE
    spectrum = np.abs(np.fft.fftshift(
        np.fft.fft(rx_norm * np.hanning(len(rx_norm))))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(rx_norm), 1 / fs))

    target_idx = int(np.argmin(np.abs(freqs - tone_freq)))
    left = max(0, target_idx - tone_bins)
    right = min(len(spectrum), target_idx + tone_bins + 1)

    in_band = float(np.sum(spectrum[left:right]))
    mask = np.ones_like(spectrum, dtype=bool)
    mask[left:right] = False
    noise = float(np.mean(spectrum[mask])) * (right - left)

    signal_power = max(in_band - noise, 1e-20)
    return float(10.0 * np.log10(signal_power / max(noise, 1e-20)))
