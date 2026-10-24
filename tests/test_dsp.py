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

import numpy as np

from pluto_siege.constants import RX_FULL_SCALE
from pluto_siege.dsp import (
    calculate_snr_db,
    dbfs_to_power,
    is_saturated,
    noise_floor_dbfs,
    power_to_dbfs,
    subwindow_powers,
)


def test_subwindow_powers_include_partial_window() -> None:
    data = np.array([1 + 2j, 3 + 4j, 5 + 6j], dtype=np.complex64)
    result = subwindow_powers(data, sub_size=2)
    expected = np.array(
        [
            (1**2 + 2**2 + 3**2 + 4**2) / (2 * RX_FULL_SCALE**2),
            (5**2 + 6**2) / RX_FULL_SCALE**2,
        ]
    )

    np.testing.assert_allclose(result, expected)


def test_power_dbfs_conversions_handle_invalid_values() -> None:
    assert power_to_dbfs(0.0) == -120.0
    assert power_to_dbfs(float("nan")) == -120.0
    assert dbfs_to_power(float("nan")) == 0.0
    assert dbfs_to_power(10.0) == 10.0


def test_noise_floor_ignores_non_finite_values() -> None:
    assert noise_floor_dbfs(np.array([float("nan"), float("inf")])) == -120.0
    result = noise_floor_dbfs(np.array([1.0, 2.0, 3.0, float("nan")]))
    assert result == power_to_dbfs(float(np.percentile([1.0, 2.0, 3.0], 25)))


def test_is_saturated_checks_iq_components() -> None:
    below = np.array([0.5 * RX_FULL_SCALE + 0j], dtype=np.complex64)
    at_limit = np.array([RX_FULL_SCALE + 0j], dtype=np.complex64)

    assert is_saturated(below) is False
    assert is_saturated(at_limit) is True


def test_calculate_snr_returns_floor_for_invalid_input() -> None:
    assert (
        calculate_snr_db(np.zeros(16, dtype=np.complex64), 1_000_000, 10_000) == -120.0
    )
    assert (
        calculate_snr_db(np.zeros(64, dtype=np.complex64), 1_000_000, 600_000) == -120.0
    )


def test_calculate_snr_detects_clean_tone() -> None:
    fs = 1_000_000.0
    n = 4096
    tone_freq = 100_000.0
    t = np.arange(n, dtype=np.float64) / fs
    signal = np.exp(2j * np.pi * tone_freq * t).astype(np.complex64)
    snr = calculate_snr_db(signal, fs, tone_freq)
    assert snr > 40.0
