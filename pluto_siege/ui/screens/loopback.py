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

from __future__ import annotations

import curses

from pluto_siege.config import CONFIG, AppConfig
from pluto_siege.constants import (
    IO_TIMEOUT_UNAVAILABLE,
    LOOPBACK_MIN_SNR_DB,
)
from pluto_siege.device import SDRDevice
from pluto_siege.engine import LoopbackTester
from pluto_siege.sound import SoundCue, play_cue
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
    screen_is_usable,
    scroll_view,
    wait_for_usable_screen,
)


def screen_loopback(
    win: curses.window, sdr: SDRDevice, config: AppConfig | None = None
) -> None:
    """Send a tone through the chip's digital loopback and measure its SNR."""
    cfg = CONFIG if config is None else config
    while True:
        if not screen_is_usable(win) and not wait_for_usable_screen(win):
            return
        log: list[tuple[str, int]] = [("Running digital loopback test...", C_DIM)]

        try:
            play_cue(SoundCue.START, enabled=cfg.enable_sound)
            start = draw_chrome(win, "Loopback Test", "Please wait...")
            if start != -1:
                _tail(log, win, start, 2)
                win.refresh()

            snr_db, tone_freq, timeout_ok = LoopbackTester.run_test(
                sdr, cfg.sample_rate, cfg.rx_buffer_size, config=cfg
            )
            if not timeout_ok:
                log.append((IO_TIMEOUT_UNAVAILABLE, C_WARN))

            log.append((f"Test tone: {tone_freq / 1000:.3f} kHz", C_DIM))
            if snr_db >= LOOPBACK_MIN_SNR_DB:
                play_cue(SoundCue.PASS, enabled=cfg.enable_sound)
                log.append((f"PASSED. SNR: {snr_db:.1f} dB", C_OK))
            else:
                play_cue(SoundCue.FAIL, enabled=cfg.enable_sound)
                log.append((f"FAILED. SNR: {snr_db:.1f} dB", C_ERR))
        except Exception as e:
            play_cue(SoundCue.FAIL, enabled=cfg.enable_sound)
            log.append((f"Loopback failed: {e}", C_ERR))

        flush_input(win)
        key = scroll_view(
            win,
            "Loopback Test",
            log,
            hint="Enter/R = retest ▎ Esc/Q = back",
            exit_keys=(KEY_ESC, ord("q"), ord("Q"), ord("r"), ord("R")) + KEY_ENTER,
        )
        if key not in (ord("r"), ord("R")) and key not in KEY_ENTER:
            return
