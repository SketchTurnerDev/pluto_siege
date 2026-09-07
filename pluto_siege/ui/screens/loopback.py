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

from typing import Optional

from pluto_siege.config import AppConfig
from pluto_siege.constants import (
    IO_TIMEOUT_UNAVAILABLE,
    LOOPBACK_MIN_SNR_DB,
)
from pluto_siege.device import SDRDevice
from pluto_siege.engine import LoopbackTester
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


def screen_loopback(
    win: "curses.window", sdr: SDRDevice, config: Optional[AppConfig] = None
) -> None:
    """Send a tone through the chip's digital loopback and measure its SNR."""
    cfg = CONFIG if config is None else config
    log: list[tuple[str, int]] = []

    def render(hint: str = "Please wait...") -> None:
        start = draw_chrome(win, "Loopback Test", hint)
        _tail(log, win, start, 2)
        win.refresh()

    try:
        log.append(("Running digital loopback test...", C_DIM))
        render()

        snr_db, tone_freq, timeout_ok = LoopbackTester.run_test(
            sdr, cfg.sample_rate, cfg.rx_buffer_size, config=cfg
        )
        if not timeout_ok:
            log.append((IO_TIMEOUT_UNAVAILABLE, C_WARN))

        log.append((f"Test tone: {tone_freq / 1000:.3f} kHz", C_DIM))
        if snr_db >= LOOPBACK_MIN_SNR_DB:
            log.append((f"PASSED. SNR: {snr_db:.1f} dB", C_OK))
        else:
            log.append((f"FAILED. SNR: {snr_db:.1f} dB", C_ERR))
    except Exception as e:
        log.append((f"Loopback failed: {e}", C_ERR))

    flush_input(win)
    scroll_view(win, "Loopback Test", log, hint="Enter/Esc = back",
                exit_keys=(KEY_ESC,) + KEY_ENTER)
