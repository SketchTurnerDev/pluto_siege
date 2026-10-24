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

from __future__ import annotations

import math
import os
import sys
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from pluto_siege.config import CONFIG, AppConfig
from pluto_siege.constants import (
    IO_TIMEOUT_FACTOR,
    IO_TIMEOUT_MIN_MS,
    MAX_RF_BW,
    RX_BUFFER_RANGE,
    RX_GAIN_RANGE,
    SAMPLE_RATE_RANGE,
    TX_GAIN_RANGE,
)

_DRIVER_IMPORT_ERROR: Exception | None = None
try:
    import adi
except (ImportError, OSError, AttributeError) as exc:
    adi = None
    _DRIVER_IMPORT_ERROR = exc


@runtime_checkable
class SDRDevice(Protocol):
    """The AD936x/pyadi-iio interface used by PlutoSiege."""

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

    def rx(self) -> NDArray[np.complex64]: ...
    def tx(self, data: NDArray[np.complex64]) -> None: ...
    def rx_destroy_buffer(self) -> None: ...
    def tx_destroy_buffer(self) -> None: ...


class SuppressCStderr:
    """Temporarily redirect stderr (fd 2) to devnull so C-level libiio logs do not corrupt the TUI."""

    def __init__(self) -> None:
        self._null = -1
        self._stderr = -1

    def __enter__(self) -> SuppressCStderr:
        try:
            sys.stderr.flush()
            self._null = os.open(os.devnull, os.O_WRONLY)
            self._stderr = os.dup(2)
            os.dup2(self._null, 2)
        except OSError:
            self._cleanup()
        return self

    def __exit__(self, *args: object) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        if self._stderr != -1:
            try:
                os.dup2(self._stderr, 2)
            except OSError:
                pass
            try:
                os.close(self._stderr)
            except OSError:
                pass
            self._stderr = -1
        if self._null != -1:
            try:
                os.close(self._null)
            except OSError:
                pass
            self._null = -1


suppress_c_stderr = SuppressCStderr


def cleanup_sdr(sdr: SDRDevice) -> None:
    """Silence the transmitter, disable loopback, and release both buffers. Never raises."""
    with suppress_c_stderr():
        for attr, value in (
            ("loopback", 0),
            ("tx_cyclic_buffer", False),
            ("tx_hardwaregain_chan0", TX_GAIN_RANGE[0]),
        ):
            try:
                setattr(sdr, attr, value)
            except Exception:
                pass
        for method_name in ("tx_destroy_buffer", "rx_destroy_buffer"):
            try:
                getattr(sdr, method_name)()
            except Exception:
                pass


def set_io_timeout(sdr: SDRDevice, expected_seconds: float) -> bool:
    """Bound a libiio transfer; returns False if the driver does not support timeouts."""
    if not math.isfinite(expected_seconds) or expected_seconds < 0:
        raise ValueError("Expected transfer duration must be finite and non-negative")
    max_ms = 2**32 - 1
    seconds = min(expected_seconds, max_ms / (1000 * IO_TIMEOUT_FACTOR))
    ms = min(max_ms, max(IO_TIMEOUT_MIN_MS, int(seconds * 1000 * IO_TIMEOUT_FACTOR)))
    try:
        sdr.ctx.set_timeout(ms)
        return True
    except (AttributeError, OSError, RuntimeError, OverflowError):
        return False


def safe_rx(sdr: SDRDevice) -> NDArray[np.complex64]:
    """Receive an owned, finite, 1D complex64 buffer or raise OSError on link failure."""
    raw = sdr.rx()
    if raw is None:
        raise OSError("SDR returned empty data (USB disconnected?)")
    try:
        with np.errstate(over="ignore", invalid="ignore"):
            data = np.array(raw, dtype=np.complex64, order="C", copy=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OSError("SDR returned invalid sample data") from exc
    if data.size == 0:
        raise OSError("SDR returned empty data (USB disconnected?)")
    if data.ndim != 1:
        raise OSError("SDR must return one channel as a one-dimensional buffer")
    if not bool(np.all(np.isfinite(data))):
        raise OSError("SDR returned non-finite samples")
    return data


def _check_number(
    value: float,
    limits: tuple[float, float],
    name: str,
    *,
    integral: bool = False,
) -> None:
    """Validate numeric parameter ranges before programming SDR hardware."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if (
        isinstance(value, (bool, str))
        or not math.isfinite(number)
        or not limits[0] <= number <= limits[1]
        or (integral and not number.is_integer())
    ):
        raise ValueError(f"{name} must be within {limits[0]:g} .. {limits[1]:g}")


def _apply_rx_settings(
    sdr: SDRDevice, use_sr: int, freq: int, buf_size: int, rx_gain: float
) -> None:
    sdr.rx_rf_bandwidth = min(use_sr, MAX_RF_BW)
    sdr.rx_lo = int(freq)
    sdr.rx_buffer_size = int(buf_size)
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0 = float(rx_gain)


def _apply_tx_settings(
    sdr: SDRDevice, use_sr: int, freq: int, tx_gain: float
) -> None:
    sdr.tx_rf_bandwidth = min(use_sr, MAX_RF_BW)
    sdr.tx_lo = int(freq)
    sdr.tx_hardwaregain_chan0 = float(tx_gain)


def cfg_rx(
    sdr: SDRDevice,
    sr: int | None = None,
    freq: int | None = None,
    config: AppConfig | None = None,
) -> bool:
    """Configure RX and return whether libiio timeout is supported."""
    cfg = CONFIG if config is None else config
    use_sr = cfg.sample_rate if sr is None else sr
    use_freq = cfg.rx_freq if freq is None else freq
    _check_number(use_sr, SAMPLE_RATE_RANGE, "Sample rate", integral=True)
    _check_number(use_freq, cfg.freq_bounds, "RX frequency", integral=True)
    _check_number(cfg.rx_buffer_size, RX_BUFFER_RANGE, "RX buffer size", integral=True)
    _check_number(cfg.rx_gain, RX_GAIN_RANGE, "RX gain")
    with suppress_c_stderr():
        cleanup_sdr(sdr)
        try:
            sdr.sample_rate = int(use_sr)
            _apply_rx_settings(
                sdr, int(use_sr), int(use_freq), cfg.rx_buffer_size, cfg.rx_gain
            )
            return set_io_timeout(sdr, cfg.rx_buffer_size / use_sr)
        except BaseException:
            cleanup_sdr(sdr)
            raise


def cfg_tx(
    sdr: SDRDevice,
    sr: int | None = None,
    freq: int | None = None,
    config: AppConfig | None = None,
) -> None:
    """Configure non-cyclic TX."""
    cfg = CONFIG if config is None else config
    use_sr = cfg.sample_rate if sr is None else sr
    use_freq = cfg.tx_freq if freq is None else freq
    _check_number(use_sr, SAMPLE_RATE_RANGE, "Sample rate", integral=True)
    _check_number(use_freq, cfg.freq_bounds, "TX frequency", integral=True)
    _check_number(cfg.tx_gain, TX_GAIN_RANGE, "TX gain")
    with suppress_c_stderr():
        cleanup_sdr(sdr)
        try:
            sdr.sample_rate = int(use_sr)
            _apply_tx_settings(sdr, int(use_sr), int(use_freq), cfg.tx_gain)
            sdr.tx_cyclic_buffer = False
        except BaseException:
            cleanup_sdr(sdr)
            raise


def cfg_loopback(sdr: SDRDevice, config: AppConfig | None = None) -> bool:
    """Configure digital loopback; returns False only when I/O timeout is unavailable."""
    cfg = CONFIG if config is None else config
    _check_number(cfg.sample_rate, SAMPLE_RATE_RANGE, "Sample rate", integral=True)
    _check_number(cfg.rx_freq, cfg.freq_bounds, "RX frequency", integral=True)
    _check_number(cfg.tx_freq, cfg.freq_bounds, "TX frequency", integral=True)
    _check_number(cfg.rx_buffer_size, RX_BUFFER_RANGE, "RX buffer size", integral=True)
    _check_number(cfg.rx_gain, RX_GAIN_RANGE, "RX gain")
    _check_number(cfg.tx_gain, TX_GAIN_RANGE, "TX gain")
    with suppress_c_stderr():
        cleanup_sdr(sdr)
        try:
            sdr.sample_rate = int(cfg.sample_rate)
            _apply_rx_settings(
                sdr, cfg.sample_rate, cfg.rx_freq, cfg.rx_buffer_size, cfg.rx_gain
            )
            _apply_tx_settings(sdr, cfg.sample_rate, cfg.tx_freq, cfg.tx_gain)
            sdr.loopback = 1
            sdr.tx_cyclic_buffer = True
            return set_io_timeout(sdr, cfg.rx_buffer_size / cfg.sample_rate)
        except BaseException:
            cleanup_sdr(sdr)
            raise


def open_sdr(uri: str, fallback_model: str = "PlutoSDR") -> tuple[SDRDevice, str]:
    """Open a PlutoSDR device and read its hardware model."""
    if adi is None:
        detail = f" ({_DRIVER_IMPORT_ERROR})" if _DRIVER_IMPORT_ERROR else ""
        if len(detail) > 40:
            detail = detail[:37] + "...)"
        raise ImportError(
            f"pyadi-iio / libiio driver is not available{detail}.\n"
            "See README.md for driver and installation instructions."
        ) from _DRIVER_IMPORT_ERROR
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError("Pluto connection URI must not be empty")
    clean_uri = uri.strip()
    try:
        with suppress_c_stderr():
            sdr = adi.Pluto(clean_uri)
    except Exception as exc:
        raise OSError(str(exc) or f"Failed to open {clean_uri}") from exc
    model = fallback_model
    try:
        value = sdr.ctx.attrs["hw_model"]
        value = value if isinstance(value, str) else value.value
        if isinstance(value, str) and value.strip():
            model = value.strip()
    except (KeyError, AttributeError, OSError, TypeError):
        pass
    return sdr, model


def release_sdr(sdr: SDRDevice | None) -> None:
    if sdr is not None:
        cleanup_sdr(sdr)
