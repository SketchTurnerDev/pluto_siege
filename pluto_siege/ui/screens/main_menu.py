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

import signal
import sys
import time
from typing import Any, Optional

try:
    import curses
except ImportError:
    print("Missing curses library.", file=sys.stderr)
    print("On Windows, please install: pip install windows-curses", file=sys.stderr)
    sys.exit(1)

try:
    import numpy as np
    import adi
except Exception as e:
    print(f"Missing dependency or libiio driver: {e}", file=sys.stderr)
    print("On Windows: please install libiio-setup.exe from Analog Devices releases.", file=sys.stderr)
    print("On Linux: run 'sudo apt install -y libiio0 libiio-dev libiio-utils'", file=sys.stderr)
    sys.exit(1)

from pluto_siege.device import SDRDevice, open_sdr, release_sdr
from pluto_siege.settings import CONFIG, save_settings
from pluto_siege.ui.widgets.framework import (
    C_DIM,
    C_ERR,
    C_OK,
    C_WARN,
    ESC_DELAY_MS,
    KEY_ENTER,
    KEY_ESC,
    _put,
    cp,
    draw_chrome,
    flush_input,
    hide_cursor,
    init_colors,
    menu_select,
    message_box,
)
from pluto_siege.ui.screens.capture import screen_capture
from pluto_siege.ui.screens.loopback import screen_loopback
from pluto_siege.ui.screens.settings import screen_settings
from pluto_siege.ui.screens.transmit import screen_transmit


def reconnect_sdr(win: "curses.window", sdr: Optional[SDRDevice], hw_model: str,
                  previous_uri: str) -> tuple[Optional[SDRDevice], str]:
    """Re-open the device after the URI changed in Settings."""
    uri = CONFIG.pluto_uri
    start_y = draw_chrome(win, "Reconnecting", "Please wait...")
    _put(win, start_y, 2, f"Connecting to PlutoSDR at {uri} ...", cp(C_DIM))
    win.refresh()
    try:
        new_sdr, model = open_sdr(uri, hw_model)
    except Exception as e:
        CONFIG.pluto_uri = previous_uri
        save_settings()
        message_box(win, "Reconnect Failed", [
            (f"Could not open {uri}: {e}", C_ERR),
            (f"Still connected to {previous_uri}.", C_WARN),
        ])
        return sdr, hw_model
    release_sdr(sdr)
    message_box(win, "Reconnected", [(f"Connected: {model} at {uri}", C_OK)])
    return new_sdr, model


def connect_screen(win: "curses.window") -> tuple[Optional[SDRDevice], str]:
    """Connect at startup, letting the user fix the URI. Returns (None, _) on exit."""
    hint = "Enter = settings ▎ R = retry ▎ Esc = exit"
    hw_model = "PlutoSDR"
    attempt = True
    err_msg = ""

    while True:
        uri = CONFIG.pluto_uri
        if attempt:
            start_y = draw_chrome(win, "Startup", hint)
            _put(win, start_y, 2, f"Connecting to PlutoSDR at {uri} ...", cp(C_DIM))
            win.refresh()
            try:
                sdr, hw_model = open_sdr(uri, hw_model)
                _put(win, start_y + 1, 2, f"Connected: {hw_model}", cp(C_OK))
                win.refresh()
                time.sleep(0.4)
                return sdr, hw_model
            except Exception as e:
                err_msg = str(e)
                attempt = False

        start_y = draw_chrome(win, "Startup", hint)
        if start_y == -1:
            win.refresh()
            time.sleep(0.1)
            continue
        _put(win, start_y, 2, f"Cannot connect: {err_msg}", cp(C_ERR))
        _put(win, start_y + 1, 2, "Check the Pluto URI in Settings, then retry.", cp(C_WARN))
        _put(win, start_y + 2, 2, "You can still open Settings to change the URI.", cp(C_WARN))
        win.refresh()

        flush_input(win)
        win.nodelay(False)
        k = win.getch()
        if k == KEY_ESC:
            return None, hw_model
        elif k in KEY_ENTER:
            screen_settings(win)
            attempt = CONFIG.pluto_uri != uri
        elif k in (ord('r'), ord('R')):
            attempt = True


def curses_app(win: "curses.window") -> None:
    hide_cursor(False)
    try:
        curses.set_escdelay(ESC_DELAY_MS)
    except (AttributeError, curses.error):
        pass
    init_colors()

    sdr, hw_model = connect_screen(win)
    if sdr is None:
        return

    items = ["Capture", "Transmit", "Loopback Test", "Settings", "Exit"]
    idx = 0
    try:
        while True:
            choice = menu_select(win, "Main Menu", items, start_idx=idx,
                                 hint="Up/Down = move ▎ Enter = select ▎ Esc = exit")
            if choice is None:
                break
            idx = choice
            if items[idx] == "Exit":
                break
            if items[idx] == "Capture":
                screen_capture(win, sdr, hw_model)
            elif items[idx] == "Transmit":
                screen_transmit(win, sdr)
            elif items[idx] == "Loopback Test":
                screen_loopback(win, sdr)
            elif items[idx] == "Settings":
                uri_before = CONFIG.pluto_uri
                screen_settings(win)
                if CONFIG.pluto_uri != uri_before:
                    sdr, hw_model = reconnect_sdr(win, sdr, hw_model, uri_before)
    except KeyboardInterrupt:
        pass
    finally:
        release_sdr(sdr)


def sigterm_handler(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


def main() -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("Requires an interactive terminal.", file=sys.stderr)
        sys.exit(1)
    signal.signal(signal.SIGTERM, sigterm_handler)
    try:
        curses.wrapper(curses_app)
    except KeyboardInterrupt:
        pass
    except curses.error as e:
        print(f"Terminal error: {e}", file=sys.stderr)
        sys.exit(1)
    print("Goodbye!")


if __name__ == "__main__":
    main()
