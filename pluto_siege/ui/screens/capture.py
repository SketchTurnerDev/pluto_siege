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

from __future__ import annotations

import curses
import os
import time

from pluto_siege.config import CONFIG, AppConfig
from pluto_siege.constants import (
    IO_TIMEOUT_UNAVAILABLE,
    RECORDS_DIR,
)
from pluto_siege.device import SDRDevice, cleanup_sdr
from pluto_siege.engine import CaptureEngine
from pluto_siege.gps import get_gps_manager
from pluto_siege.sound import SoundCue, play_cue
from pluto_siege.ui.widgets.framework import (
    C_DIM,
    C_ERR,
    C_OK,
    C_WARN,
    KEY_ENTER,
    KEY_ESC,
    METER_REFRESH_SECONDS,
    _tail,
    draw_chrome,
    draw_gps_status,
    draw_signal_meter,
    flush_input,
    screen_is_usable,
    scroll_view,
    wait_for_usable_screen,
)

ABORT_HINT = "Esc/Q = abort"


def do_capture(
    win: curses.window,
    sdr: SDRDevice,
    hw_model: str,
    config: AppConfig | None = None,
) -> int:
    """Execute a single capture cycle. Returns the exit key pressed in the log view."""
    cfg = CONFIG if config is None else config
    if not screen_is_usable(win) and not wait_for_usable_screen(win):
        return KEY_ESC
    log: list[tuple[str, int]] = []

    def render_log(hint: str) -> tuple[int, int]:
        start = draw_chrome(win, "Capture Mode", hint)
        if start == -1:
            return -1, 0
        return start, _tail(log, win, start, 4)

    def finish(title: str, hint: str = "Enter/R = capture again ▎ Esc/Q = back") -> int:
        flush_input(win)
        return scroll_view(
            win,
            title,
            log,
            hint=hint,
            exit_keys=(KEY_ESC, ord("q"), ord("Q"), ord("r"), ord("R")) + KEY_ENTER,
        )

    try:
        log.append(("Configuring receiver...", C_DIM))
        render_log("Please wait...")
        win.refresh()

        engine = CaptureEngine(sdr, config=cfg)
        engine.prepare()

        if not engine.io_timeout_available:
            log.append((IO_TIMEOUT_UNAVAILABLE, C_WARN))

        if engine.actual_sr > 4_000_000:
            log.append(("SR > 4 MSPS. USB 2.0 may drop samples.", C_WARN))

        freq_str = f"{engine.actual_freq / 1e6:.3f} MHz"
        sr_str = f"{engine.actual_sr / 1e6:.2f} MSPS"
        thr_str = f"Threshold: {engine.trigger_threshold:.1f} dBFS"
        log.append((f"{freq_str}  {sr_str}  {thr_str}", C_DIM))
        if engine.trigger_threshold >= 0.0:
            log.append(("Threshold >= 0 dBFS: nothing can trigger.", C_WARN))
        log.append(("Listening...", C_DIM))
        play_cue(SoundCue.START, enabled=cfg.enable_sound)

        os.makedirs(RECORDS_DIR, exist_ok=True)
        flush_input(win)
        win.nodelay(True)
        last_draw = [0.0]

        start, shown = render_log(ABORT_HINT)
        meter_row = start + shown + 1

        def render_live_widgets(lvl: float = -100.0, saturated: bool = False) -> None:
            if start == -1 or not screen_is_usable(win):
                draw_chrome(win, "Capture Mode", ABORT_HINT)
                win.refresh()
                return
            draw_signal_meter(
                win,
                meter_row,
                lvl,
                engine.trigger_threshold,
                saturated,
            )
            if cfg.enable_gps:
                mgr = get_gps_manager()
                draw_gps_status(
                    win,
                    meter_row + 1,
                    mgr.get_status_text(),
                    has_fix=mgr.get_latest_fix().has_fix,
                )
            win.refresh()

        render_live_widgets()

        def check_abort() -> bool:
            nonlocal start, shown, meter_row
            k = win.getch()
            if k == curses.KEY_RESIZE:
                start, shown = render_log(ABORT_HINT)
                meter_row = start + shown + 1
                render_live_widgets(engine.current_level_dbfs, engine.current_saturated)
            return k in (KEY_ESC, ord("q"), ord("Q"))

        def on_trigger_detected() -> None:
            nonlocal start, shown, meter_row
            play_cue(SoundCue.TRIGGER, enabled=cfg.enable_sound)
            log.append(("Key detected! Capturing...", C_DIM))
            start, shown = render_log(ABORT_HINT)
            meter_row = start + shown + 1
            render_live_widgets(engine.current_level_dbfs, engine.current_saturated)

        def on_meter_update(lvl: float, saturated: bool) -> None:
            now = time.monotonic()
            if now - last_draw[0] > METER_REFRESH_SECONDS:
                render_live_widgets(lvl, saturated)
                last_draw[0] = now

        engine.listen_and_capture(
            check_abort=check_abort,
            on_trigger_detected=on_trigger_detected,
            on_meter_update=on_meter_update,
        )

        flush_input(win)

        if engine.is_aborted:
            play_cue(SoundCue.ALERT, enabled=cfg.enable_sound)
            log.append(("Capture aborted.", C_WARN))
            return finish("Capture Aborted")

        if engine.captured_data is None or engine.start_time is None:
            log.append(("No signal captured.", C_WARN))
            return finish("Capture")

        if engine.is_partial:
            log.append(("Saving partial recording...", C_WARN))

        if engine.current_saturated:
            log.append(("SATURATION detected. Lower RX gain.", C_WARN))

        gps_fix = get_gps_manager().get_latest_fix() if cfg.enable_gps else None
        base = engine.save_recording(RECORDS_DIR, hw_model, gps_fix=gps_fix)
        if gps_fix is not None and gps_fix.has_fix:
            lat_card = "N" if gps_fix.latitude >= 0 else "S"
            lon_card = "E" if gps_fix.longitude >= 0 else "W"
            log.append(
                (
                    f"Geotag: {abs(gps_fix.latitude):.5f}°{lat_card}, "
                    f"{abs(gps_fix.longitude):.5f}°{lon_card} ({gps_fix.satellites} sats)",
                    C_DIM,
                )
            )
        play_cue(SoundCue.CAPTURE_SUCCESS, enabled=cfg.enable_sound)
        log.append((f"Saved: {os.path.basename(base)}.sigmf-data", C_DIM))
        log.append(("Key captured successfully!", C_OK))
        return finish("Capture Complete")

    except (OSError, RuntimeError, ValueError) as e:
        play_cue(SoundCue.FAIL, enabled=cfg.enable_sound)
        log.append((f"Capture failed: {e}", C_ERR))
        return finish("Capture Failed", hint="Enter/R = retry ▎ Esc/Q = back")
    finally:
        try:
            win.nodelay(False)
        except curses.error:
            pass
        cleanup_sdr(sdr)


def screen_capture(
    win: curses.window,
    sdr: SDRDevice,
    hw_model: str,
    config: AppConfig | None = None,
) -> None:
    """Listen for a burst, record with pre-trigger history, and save it as SigMF."""
    while True:
        key = do_capture(win, sdr, hw_model, config=config)
        if key not in KEY_ENTER and key not in (ord("r"), ord("R")):
            break


__all__ = ["do_capture", "screen_capture"]
