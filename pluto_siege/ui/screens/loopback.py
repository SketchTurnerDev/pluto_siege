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

"""Loopback test screen: digital loopback tone + SNR measurement."""

import curses
import time

import numpy as np

from pluto_siege.constants import (
    IO_TIMEOUT_UNAVAILABLE,
    LOOPBACK_MIN_SNR_DB,
    LOOPBACK_TONE_HZ,
    LOOPBACK_TX_AMPLITUDE,
    TX_DAC_MAX,
)
from pluto_siege.device import SDRDevice, cfg_loopback, cleanup_sdr, release_sdr, safe_rx, suppress_c_stderr
from pluto_siege.dsp import calculate_snr_db
from pluto_siege.settings import CONFIG
from pluto_siege.ui.widgets.framework import (
    C_DIM,
    C_ERR,
    C_OK,
    C_WARN,
    KEY_ENTER,
    KEY_ESC,
    _tail,
    draw_chrome,
    flush_input,
    scroll_view,
)


def screen_loopback(win: "curses.window", sdr: SDRDevice) -> None:
    """Send a tone through the chip's digital loopback and measure its SNR."""
    log: list[tuple[str, int]] = []

    def render(hint: str = "Please wait...") -> None:
        start = draw_chrome(win, "Loopback Test", hint)
        _tail(log, win, start, 2)
        win.refresh()

    try:
        try:
            log.append(("Enabling digital loopback...", C_DIM))
            render()
            if not cfg_loopback(sdr):
                log.append((IO_TIMEOUT_UNAVAILABLE, C_WARN))
            fs = int(sdr.sample_rate)

            n_samples = int(CONFIG.rx_buffer_size)
            tone_bin = max(1, round(LOOPBACK_TONE_HZ * n_samples / fs))
            tone_freq = tone_bin * fs / n_samples
            log.append((f"Test tone: {tone_freq / 1000:.3f} kHz", C_DIM))
            render()

            n = np.arange(n_samples, dtype=np.float64)
            tx = (LOOPBACK_TX_AMPLITUDE * np.exp(2j * np.pi * tone_bin * n / n_samples)
                  * TX_DAC_MAX).astype(np.complex64)

            log.append(("TX + RX (flushing)...", C_DIM))
            render()
            with suppress_c_stderr():
                sdr.tx(tx)
            time.sleep(0.05)
            for _ in range(2):
                safe_rx(sdr)
            rx = safe_rx(sdr)

            snr_db = calculate_snr_db(rx, fs, tone_freq)

            if snr_db >= LOOPBACK_MIN_SNR_DB:
                log.append((f"PASSED. SNR: {snr_db:.1f} dB", C_OK))
            else:
                log.append((f"FAILED. SNR: {snr_db:.1f} dB", C_ERR))
        finally:
            cleanup_sdr(sdr)
    except Exception as e:
        log.append((f"Loopback failed: {e}", C_ERR))

    flush_input(win)
    scroll_view(win, "Loopback Test", log, hint="Enter/Esc = back",
                exit_keys=(KEY_ESC,) + KEY_ENTER)
