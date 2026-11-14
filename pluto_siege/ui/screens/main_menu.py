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

"""Main menu, startup connection screen, and application entry point."""

from __future__ import annotations

import atexit
import curses
import threading
import time

from pluto_siege.cli import (
    CLIOptions,
    main,
    resolve_transmit_target,
    sig_handler,
)
from pluto_siege.config import CONFIG, AppConfig, save_settings
from pluto_siege.device import SDRDevice, open_sdr, release_sdr
from pluto_siege.gps import init_gps, stop_gps
from pluto_siege.sound import SoundCue, play_cue
from pluto_siege.ui.screens.capture import screen_capture
from pluto_siege.ui.screens.loopback import screen_loopback
from pluto_siege.ui.screens.settings import screen_settings
from pluto_siege.ui.screens.transmit import do_transmit, screen_transmit
from pluto_siege.ui.widgets.framework import (
    BUTTON_CLICK,
    BUTTON_DOUBLE,
    BUTTON_RIGHT,
    C_DIM,
    C_ERR,
    C_OK,
    C_WARN,
    CONTENT_X,
    ESC_DELAY_MS,
    KEY_ENTER,
    KEY_ESC,
    WHEEL_DOWN,
    WHEEL_UP,
    HitBox,
    _move_cursor,
    _put,
    cp,
    draw_chrome,
    draw_list_row,
    flush_input,
    get_mouse_event,
    init_colors,
    init_mouse,
    menu_select,
    message_box,
    screen_is_usable,
    set_cursor_visible,
    wait_for_usable_screen,
)

_ACTIVE_SDR: SDRDevice | None = None
_SDR_LOCK = threading.Lock()


def _set_active_sdr(sdr: SDRDevice | None) -> None:
    global _ACTIVE_SDR
    with _SDR_LOCK:
        _ACTIVE_SDR = sdr


def _cleanup_active_sdr() -> None:
    global _ACTIVE_SDR
    with _SDR_LOCK:
        sdr = _ACTIVE_SDR
        _ACTIVE_SDR = None
    if sdr is not None:
        try:
            release_sdr(sdr)
        except (OSError, RuntimeError, AttributeError):
            pass
    stop_gps()


atexit.register(_cleanup_active_sdr)


def reconnect_sdr(
    win: curses.window,
    sdr: SDRDevice | None,
    hw_model: str,
    previous_uri: str,
    config: AppConfig | None = None,
) -> tuple[SDRDevice | None, str]:
    """Re-open the device after the URI changed in Settings."""
    cfg = CONFIG if config is None else config
    uri = cfg.pluto_uri
    if not screen_is_usable(win) and not wait_for_usable_screen(win):
        cfg.update({"pluto_uri": previous_uri})
        save_settings(cfg)
        return sdr, hw_model
    start_y = draw_chrome(win, "Reconnecting", "Please wait...")
    _put(win, start_y, CONTENT_X, f"Connecting to PlutoSDR at {uri} ...", cp(C_DIM))
    win.refresh()
    _set_active_sdr(None)
    release_sdr(sdr)
    try:
        new_sdr, model = open_sdr(uri, hw_model)
    except Exception as e:
        cfg.update({"pluto_uri": previous_uri})
        save_settings(cfg)
        prev_sdr = None
        try:
            prev_sdr, _ = open_sdr(previous_uri, hw_model)
            _set_active_sdr(prev_sdr)
        except Exception:
            pass
        play_cue(SoundCue.FAIL, enabled=cfg.enable_sound)
        if prev_sdr is not None:
            message_box(
                win,
                "Reconnect Failed",
                [
                    (f"Could not open {uri}: {e}", C_ERR),
                    ("Please check Pluto connection settings.", C_WARN),
                    (f"Reverted to {previous_uri}.", C_WARN),
                ],
            )
        else:
            message_box(
                win,
                "Reconnect Failed",
                [
                    (f"Could not open {uri}: {e}", C_ERR),
                    ("Please check Pluto connection settings.", C_WARN),
                    (f"Could not revert to {previous_uri}.", C_ERR),
                    ("Device disconnected.", C_WARN),
                ],
            )
        return prev_sdr, hw_model
    _set_active_sdr(new_sdr)
    init_gps(cfg)
    play_cue(SoundCue.SUCCESS, enabled=cfg.enable_sound)
    message_box(win, "Reconnected", [(f"Connected: PlutoSDR ({uri})", C_OK)])
    return new_sdr, model


def is_sdr_connected(sdr: SDRDevice | None) -> bool:
    """Check if the SDR device is still responsive."""
    if sdr is None:
        return False
    try:
        _ = sdr.sample_rate
        return True
    except (OSError, RuntimeError, AttributeError):
        return False


def connect_screen(
    win: curses.window, config: AppConfig | None = None
) -> tuple[SDRDevice | None, str]:
    """Connect at startup, letting the user fix the URI. Returns (None, _) on exit."""
    cfg = CONFIG if config is None else config
    menu_hint = "Up/Down = move ▎ Enter = select ▎ R = retry ▎ S = settings ▎ Esc/Q = quit"
    items = ["Retry Connection", "Settings", "Exit"]
    idx = 0
    hw_model = "PlutoSDR"
    attempt = True
    err_msg = ""

    while True:
        if not screen_is_usable(win) and not wait_for_usable_screen(win):
            return None, hw_model
        uri = cfg.pluto_uri
        if attempt:
            start_y = draw_chrome(win, "Startup", "Please wait...")
            _put(
                win,
                start_y,
                CONTENT_X,
                f"Connecting to PlutoSDR at {uri} ...",
                cp(C_DIM),
            )
            win.refresh()
            try:
                sdr, hw_model = open_sdr(uri, hw_model)
                _set_active_sdr(sdr)
                init_gps(cfg)
                _put(win, start_y + 1, CONTENT_X, f"Connected: PlutoSDR ({uri})", cp(C_OK))
                win.refresh()
                time.sleep(0.4)
                return sdr, hw_model
            except Exception as e:
                err_msg = str(e)
                attempt = False

        start_y = draw_chrome(win, "Startup", menu_hint)
        if start_y == -1:
            win.refresh()
            k = win.getch()
            if k in (KEY_ESC, ord("q"), ord("Q")):
                return None, hw_model
            continue
        err_lines = [line.strip() for line in err_msg.splitlines() if line.strip()][:4]
        curr_y = start_y
        if len(err_lines) <= 1:
            msg = err_lines[0] if err_lines else "Unknown error"
            _put(win, curr_y, CONTENT_X, f"Cannot connect to {uri}: {msg}", cp(C_ERR))
            curr_y += 1
        else:
            _put(win, curr_y, CONTENT_X, f"Cannot connect to {uri}:", cp(C_ERR))
            curr_y += 1
            for el in err_lines:
                _put(win, curr_y, CONTENT_X + 2, el, cp(C_ERR))
                curr_y += 1
        curr_y += 1
        _put(
            win,
            curr_y,
            CONTENT_X,
            "Check the Pluto connection settings or connect the device, then retry.",
            cp(C_WARN),
        )
        menu_start = curr_y + 2
        hitboxes: dict[int, HitBox] = {}
        for i, label in enumerate(items):
            hitboxes[i] = draw_list_row(win, menu_start + i, label, i == idx)
        win.refresh()

        flush_input(win)
        win.nodelay(False)
        k = win.getch()
        selected_action: str | None = None
        if k in (KEY_ESC, ord("q"), ord("Q")):
            return None, hw_model
        elif k == getattr(curses, "KEY_RESIZE", -1):
            continue
        elif k in KEY_ENTER:
            selected_action = items[idx]
        elif k in (ord("r"), ord("R"), ord("1")):
            idx = 0
            selected_action = "Retry Connection"
        elif k in (ord("s"), ord("S"), ord("2")):
            idx = 1
            selected_action = "Settings"
        elif k in (ord("e"), ord("E"), ord("3")):
            idx = 2
            selected_action = "Exit"
        elif k == getattr(curses, "KEY_MOUSE", -1):
            m = get_mouse_event()
            if m is not None:
                _mid, mx, my, _mz, bstate = m
                if bstate & WHEEL_UP:
                    idx = max(0, idx - 1)
                elif bstate & WHEEL_DOWN:
                    idx = min(len(items) - 1, idx + 1)
                elif bstate & BUTTON_RIGHT:
                    return None, hw_model
                elif bstate & (BUTTON_CLICK | BUTTON_DOUBLE):
                    clicked_idx = my - menu_start
                    hitbox = hitboxes.get(clicked_idx)
                    if hitbox is not None and hitbox.contains(mx, my):
                        if clicked_idx == idx or (bstate & BUTTON_DOUBLE):
                            selected_action = items[clicked_idx]
                        else:
                            idx = clicked_idx
        else:
            idx = _move_cursor(idx, k, len(items))

        if selected_action == "Retry Connection":
            attempt = True
        elif selected_action == "Settings":
            screen_settings(win, config=cfg)
            init_gps(cfg)
            attempt = cfg.pluto_uri != uri
            if not attempt:
                idx = 0
        elif selected_action == "Exit":
            return None, hw_model


def curses_app(
    win: curses.window,
    config: AppConfig | None = None,
    cli_opts: CLIOptions | None = None,
) -> None:
    """Run the initialized UI lifecycle, always restoring terminal/device state."""
    cfg = CONFIG if config is None else config
    try:
        try:
            win.keypad(True)
        except curses.error:
            pass
        set_cursor_visible(False)
        try:
            curses.set_escdelay(ESC_DELAY_MS)
        except (AttributeError, curses.error):
            pass
        init_colors()
        init_mouse(cfg.enable_mouse)
        init_gps(cfg)

        sdr, hw_model = connect_screen(win, config=cfg)
        if sdr is None:
            return

        items = [
            "Capture",
            "Transmit",
            "Loopback Test",
            "Settings",
            "Exit",
        ]
        idx = 0

        if cli_opts is not None and cli_opts.mode is not None:
            if cli_opts.mode == "capture":
                idx = 0
                screen_capture(win, sdr, hw_model, config=cfg)
            elif cli_opts.mode == "transmit":
                idx = 1
                if cli_opts.transmit_path:
                    try:
                        tx_path = resolve_transmit_target(
                            cli_opts.transmit_path, cfg.records_dir
                        )
                        while True:
                            key = do_transmit(win, sdr, tx_path, config=cfg)
                            if key not in KEY_ENTER and key not in (
                                ord("r"),
                                ord("R"),
                            ):
                                break
                    except FileNotFoundError as err:
                        message_box(win, "Transmit", [(str(err), C_ERR)])
                else:
                    screen_transmit(win, sdr, config=cfg)
            elif cli_opts.mode == "loopback":
                idx = 2
                screen_loopback(win, sdr, config=cfg)

            if not is_sdr_connected(sdr):
                _cleanup_active_sdr()
                sdr = None
        while True:
            choice = menu_select(
                win,
                "Main Menu",
                items,
                start_idx=idx,
                hint="Up/Down = move ▎ Enter = select ▎ Esc/Q = quit",
                hotkeys=True,
            )
            if choice is None:
                break
            idx = choice
            if items[idx] == "Exit":
                break
            if items[idx] in ("Capture", "Transmit", "Loopback Test"):
                if not is_sdr_connected(sdr):
                    _cleanup_active_sdr()
                    sdr = None
                    play_cue(SoundCue.ALERT, enabled=cfg.enable_sound)
                    message_box(
                        win,
                        "SDR Disconnected",
                        [
                            (
                                "SDR device is not responding (USB disconnected?).",
                                C_ERR,
                            ),
                            ("Opening connection screen...", C_WARN),
                        ],
                    )
                    new_sdr, new_hw = connect_screen(win, config=cfg)
                    if new_sdr is None:
                        continue
                    sdr, hw_model = new_sdr, new_hw

                if sdr is None:
                    continue

                if items[idx] == "Capture":
                    screen_capture(win, sdr, hw_model, config=cfg)
                elif items[idx] == "Transmit":
                    screen_transmit(win, sdr, config=cfg)
                elif items[idx] == "Loopback Test":
                    screen_loopback(win, sdr, config=cfg)

                if not is_sdr_connected(sdr):
                    _cleanup_active_sdr()
                    sdr = None
            elif items[idx] == "Settings":
                uri_before = cfg.pluto_uri
                screen_settings(win, config=cfg)
                init_gps(cfg)
                if cfg.pluto_uri != uri_before:
                    new_sdr, new_hw = reconnect_sdr(
                        win, sdr, hw_model, uri_before, config=cfg
                    )
                    if new_sdr is None:
                        new_sdr, new_hw = connect_screen(win, config=cfg)
                        if new_sdr is None:
                            break
                    sdr, hw_model = new_sdr, new_hw
    except KeyboardInterrupt:
        pass
    finally:
        set_cursor_visible(True)
        _cleanup_active_sdr()


__all__ = [
    "connect_screen",
    "curses_app",
    "is_sdr_connected",
    "main",
    "reconnect_sdr",
    "sig_handler",
]


if __name__ == "__main__":
    main()
