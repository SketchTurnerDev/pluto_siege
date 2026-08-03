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

"""Capture screen: UI view layer that delegates signal capture to CaptureEngine."""

import curses
import os
import time

from pluto_siege.constants import (
    IO_TIMEOUT_UNAVAILABLE,
    RECORDS_DIR,
)
from pluto_siege.device import cleanup_sdr
from pluto_siege.engine import CaptureEngine
from pluto_siege.settings import CONFIG
from pluto_siege.sigmf import save_sigmf_pair, to_sigmf_utc
from pluto_siege.ui.widgets.framework import (
    C_DIM,
    C_ERR,
    C_OK,
    C_WARN,
    KEY_ENTER,
    KEY_ESC,
    METER_REFRESH_SECONDS,
    _tail,
    curses,
    draw_chrome,
    draw_signal_meter,
    flush_input,
    scroll_view,
)


def screen_capture(win: "curses.window", sdr, hw_model: str) -> None:
    """Listen for a burst, record with pre-trigger history, and save it as SigMF."""
    log: list[tuple[str, int]] = []

    def render_log(hint: str) -> tuple[int, int]:
        start = draw_chrome(win, "Capture Mode", hint)
        return start, _tail(log, win, start, 4)

    def finish(title: str) -> None:
        scroll_view(
            win, title, log, hint="Enter/Esc = back", exit_keys=(KEY_ESC,) + KEY_ENTER
        )

    try:
        log.append(("Configuring receiver...", C_DIM))
        render_log("Please wait...")
        win.refresh()

        engine = CaptureEngine(sdr)
        engine.prepare()

        if not engine.io_timeout_available:
            log.append((IO_TIMEOUT_UNAVAILABLE, C_WARN))

        if engine.actual_sr > 4_000_000:
            log.append(("SR > 4 MSPS. USB 2.0 may drop samples.", C_WARN))

        log.append(
            (f"{engine.actual_freq / 1e6:.3f} MHz  {engine.actual_sr / 1e6:.2f} MSPS  Threshold: {engine.trigger_threshold:.1f} dBFS", C_DIM)
        )
        if engine.trigger_threshold >= 0.0:
            log.append(("Threshold >= 0 dBFS: nothing can trigger.", C_WARN))
        log.append(("Listening...", C_DIM))

        os.makedirs(RECORDS_DIR, exist_ok=True)
        flush_input(win)
        win.nodelay(True)
        last_draw = [0.0]

        def check_abort() -> bool:
            return win.getch() == KEY_ESC

        def on_trigger_detected() -> None:
            log.append(("Key detected! Capturing...", C_DIM))
            render_log("Esc = abort")
            win.refresh()

        def on_meter_update(lvl: float, saturated: bool) -> None:
            now = time.monotonic()
            if now - last_draw[0] > METER_REFRESH_SECONDS:
                start, shown = render_log("Esc = abort")
                draw_signal_meter(
                    win,
                    start + shown + 1,
                    lvl,
                    engine.trigger_threshold,
                    saturated,
                )
                win.refresh()
                last_draw[0] = now

        engine.listen_and_capture(
            check_abort=check_abort,
            on_trigger_detected=on_trigger_detected,
            on_meter_update=on_meter_update,
        )

        flush_input(win)
        win.nodelay(False)
        cleanup_sdr(sdr)

        if engine.is_aborted:
            log.append(("Capture aborted.", C_WARN))
            finish("Capture Aborted")
            return

        if engine.captured_data is None or engine.start_time is None:
            log.append(("No signal captured.", C_WARN))
            finish("Capture")
            return

        if engine.is_partial:
            log.append(("Saving partial recording...", C_WARN))

        if engine.current_saturated:
            log.append(("SATURATION detected. Lower RX gain.", C_WARN))

        ts = engine.start_time.strftime("%Y%m%d_%H%M%S_%f")
        base = os.path.join(
            RECORDS_DIR, f"rec_{ts}_{engine.actual_freq}_{engine.actual_sr}"
        )
        save_sigmf_pair(
            base,
            engine.captured_data,
            engine.actual_freq,
            engine.actual_sr,
            to_sigmf_utc(engine.start_time),
            hw_model,
        )
        log.append(
            (
                f"Saved: {os.path.basename(base)}.sigmf-data",
                C_DIM,
            )
        )
        log.append(("Key captured successfully!", C_OK))
        finish("Capture Complete")

    except Exception as e:
        try:
            win.nodelay(False)
        except Exception:
            pass
        cleanup_sdr(sdr)
        log.append((f"Capture failed: {e}", C_ERR))
        finish("Capture Failed")
