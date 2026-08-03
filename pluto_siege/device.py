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

"""PlutoSDR device access: open, configure, cleanup, and SDRDevice protocol."""

from typing import Any, Optional, Protocol, Tuple, runtime_checkable

import os
import numpy as np

try:
    import adi
except Exception:
    adi = None  # type: ignore[assignment]

from pluto_siege.constants import (
    IO_TIMEOUT_FACTOR,
    IO_TIMEOUT_MIN_MS,
    MAX_RF_BW,
    TX_GAIN_RANGE,
)
from pluto_siege.settings import CONFIG


@runtime_checkable
class SDRDevice(Protocol):
    """Abstract protocol for SDR hardware transceivers (ADALM-Pluto, HackRF, etc.)."""

    sample_rate: int
    rx_lo: int
    tx_lo: int
    rx_buffer_size: int
    rx_rf_bandwidth: int
    tx_rf_bandwidth: int
    rx_hardwaregain_chan0: float
    tx_hardwaregain_chan0: float
    gain_control_mode_chan0: str
    tx_cyclic_buffer: bool
    loopback: int
    ctx: Any

    def rx(self) -> np.ndarray: ...
    def tx(self, data: np.ndarray) -> None: ...
    def rx_destroy_buffer(self) -> None: ...
    def tx_destroy_buffer(self) -> None: ...


class suppress_c_stderr:
    """Redirect low-level C stderr (fd 2) to devnull to prevent C libiio logs from corrupting curses TUI."""
    def __enter__(self) -> None:
        try:
            self._null = os.open(os.devnull, os.O_WRONLY)
            self._stderr = os.dup(2)
            os.dup2(self._null, 2)
        except Exception:
            self._null = -1
            self._stderr = -1

    def __exit__(self, *args: Any) -> None:
        if self._stderr != -1:
            try:
                os.dup2(self._stderr, 2)
                os.close(self._stderr)
                os.close(self._null)
            except Exception:
                pass


def cleanup_sdr(sdr: SDRDevice) -> None:
    """Silence the transmitter, disable loopback, and release both buffers. Never raises."""
    with suppress_c_stderr():
        try:
            sdr.loopback = 0
        except Exception:
            pass
        try:
            sdr.tx_cyclic_buffer = False
        except Exception:
            pass
        try:
            sdr.tx_hardwaregain_chan0 = TX_GAIN_RANGE[0]
        except Exception:
            pass
        try:
            sdr.tx_destroy_buffer()
        except Exception:
            pass
        try:
            sdr.rx_destroy_buffer()
        except Exception:
            pass


def set_io_timeout(sdr: SDRDevice, expected_seconds: float) -> bool:
    """Bound blocking transfers so a wedged link fails instead of hanging."""
    ms = max(IO_TIMEOUT_MIN_MS, int(expected_seconds * 1000 * IO_TIMEOUT_FACTOR))
    try:
        sdr.ctx.set_timeout(ms)
        return True
    except Exception:
        return False


def safe_rx(sdr: SDRDevice) -> np.ndarray:
    """Receive one buffer as contiguous complex64. Raises if the link is dead."""
    with suppress_c_stderr():
        data = sdr.rx()
    if data is None or len(data) == 0:
        raise IOError("SDR returned empty data (USB disconnected?)")
    return np.ascontiguousarray(data, dtype=np.complex64)


def _apply_rx_settings(sdr: SDRDevice, use_sr: int, freq: int, buf_size: int) -> None:
    """Receiver half of a configuration, shared by capture and loopback."""
    sdr.rx_rf_bandwidth = min(use_sr, MAX_RF_BW)
    sdr.rx_lo = int(freq)
    sdr.rx_buffer_size = int(buf_size)
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0 = float(CONFIG.rx_gain)


def _apply_tx_settings(sdr: SDRDevice, use_sr: int, freq: int) -> None:
    """Transmitter half of a configuration, shared by replay and loopback."""
    sdr.tx_rf_bandwidth = min(use_sr, MAX_RF_BW)
    sdr.tx_lo = int(freq)
    sdr.tx_hardwaregain_chan0 = float(CONFIG.tx_gain)


def cfg_rx(sdr: SDRDevice, sr: Optional[int] = None, freq: Optional[int] = None) -> bool:
    """Configure the receiver. Returns False if the I/O timeout is unavailable."""
    with suppress_c_stderr():
        cleanup_sdr(sdr)
        use_sr = int(CONFIG.sample_rate if sr is None else sr)
        buf_size = int(CONFIG.rx_buffer_size)
        sdr.sample_rate = use_sr
        _apply_rx_settings(
            sdr, use_sr, CONFIG.rx_freq if freq is None else freq, buf_size
        )
        return set_io_timeout(sdr, buf_size / use_sr)


def cfg_tx(sdr: SDRDevice, sr: Optional[int] = None, freq: Optional[int] = None) -> None:
    """Configure the transmitter."""
    with suppress_c_stderr():
        cleanup_sdr(sdr)
        use_sr = int(CONFIG.sample_rate if sr is None else sr)
        sdr.sample_rate = use_sr
        _apply_tx_settings(
            sdr, use_sr, CONFIG.tx_freq if freq is None else freq
        )
        sdr.tx_cyclic_buffer = False


def cfg_loopback(sdr: SDRDevice) -> bool:
    """Configure the digital loopback. Returns False if no I/O timeout."""
    with suppress_c_stderr():
        cleanup_sdr(sdr)
        use_sr = int(CONFIG.sample_rate)
        buf_size = int(CONFIG.rx_buffer_size)
        sdr.sample_rate = use_sr
        _apply_rx_settings(sdr, use_sr, CONFIG.rx_freq, buf_size)
        _apply_tx_settings(sdr, use_sr, CONFIG.tx_freq)
        sdr.loopback = 1
        sdr.tx_cyclic_buffer = True
        return set_io_timeout(sdr, buf_size / use_sr)


def open_sdr(uri: str, fallback_model: str = "PlutoSDR") -> Tuple[SDRDevice, str]:
    """Open a device and read its hardware model. Raises on failure."""
    if adi is None:
        raise ImportError(
            "pyadi-iio / libiio driver is not available.\n"
            "On Windows: install libiio-setup.exe from Analog Devices releases.\n"
            "On Linux: run 'sudo apt install -y libiio0 libiio-dev libiio-utils'"
        )
    with suppress_c_stderr():
        sdr = adi.Pluto(uri)
    model = fallback_model
    try:
        model = sdr.ctx.attrs["hw_model"].value
    except Exception:
        pass
    return sdr, model


def release_sdr(sdr: Optional[SDRDevice]) -> None:
    if sdr is None:
        return
    with suppress_c_stderr():
        cleanup_sdr(sdr)
        try:
            del sdr
        except Exception:
            pass
