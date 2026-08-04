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

"""Curses UI framework: colours, drawing primitives, menus, text editors."""

import curses
import math
import time
from typing import Any, Optional

import numpy as np

from pluto_siege.constants import LOGO_LINES

# ---------------------------------------------------------------------------
# Colour pairs
# ---------------------------------------------------------------------------

C_TITLE = 1
C_ACCENT = 2
C_OK = 3
C_WARN = 4
C_ERR = 5
C_DIM = 6
C_BAR = 7

FLASH_SECONDS = 0.9
METER_REFRESH_SECONDS = 0.1
METER_FLOOR_DBFS = -80.0
KEY_ENTER = (10, 13)
KEY_ESC = 27
ESC_DELAY_MS = 25


def init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    try:
        curses.init_pair(C_TITLE, curses.COLOR_CYAN, bg)
        curses.init_pair(C_ACCENT, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(C_OK, curses.COLOR_GREEN, bg)
        curses.init_pair(C_WARN, curses.COLOR_YELLOW, bg)
        curses.init_pair(C_ERR, curses.COLOR_RED, bg)
        curses.init_pair(C_DIM, curses.COLOR_WHITE, bg)
        curses.init_pair(C_BAR, curses.COLOR_GREEN, bg)
    except curses.error:
        pass


def cp(pair: int) -> int:
    return curses.color_pair(pair) if curses.has_colors() else 0


def hide_cursor(visible: bool) -> None:
    """Show or hide the caret. Terminals may refuse, which is not an error."""
    try:
        curses.curs_set(1 if visible else 0)
    except curses.error:
        pass


def flush_input(win: Optional["curses.window"] = None) -> None:
    """Purge all pending keyboard input from Curses and OS input buffers."""
    try:
        curses.flushinp()
    except curses.error:
        pass
    if win is not None:
        try:
            win.nodelay(True)
            while win.getch() != -1:
                pass
            win.nodelay(False)
        except curses.error:
            pass
    try:
        curses.flushinp()
    except curses.error:
        pass



def _put(win: "curses.window", y: int, x: int, text: str, attr: int = 0) -> None:
    """Draw text, clipped to the window. Off-screen writes are dropped."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    text = text[: max(0, w - 1 - x)]
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def _draw_hint_line(win: "curses.window", y: int, hint: str) -> None:
    """Draw footer hint line framed with cyan vertical block '▎' at start and end."""
    hint_str = hint.strip()
    if not hint_str:
        return
    full_hint = f"▎ {hint_str}"
    parts = full_hint.split("▎")
    curr_x = 2
    for i, part in enumerate(parts):
        if part:
            _put(win, y, curr_x, part, cp(C_DIM) | curses.A_DIM)
            curr_x += len(part)
        if i < len(parts) - 1:
            _put(win, y, curr_x, "▎", cp(C_TITLE) | curses.A_BOLD)
            curr_x += 1


MIN_WIN_HEIGHT = 31
MIN_WIN_WIDTH = 90


def update_term_size() -> None:
    """Synchronize curses screen buffer size with actual terminal dimensions on Windows."""
    try:
        curses.resize_term(0, 0)
    except Exception:
        pass


def draw_chrome(win: "curses.window", subtitle: str = "", hint: str = "") -> int:
    """Draw logo, divider and footer hint. Returns the first free content row, or -1 if too small."""
    update_term_size()
    win.erase()
    h, w = win.getmaxyx()
    if h < MIN_WIN_HEIGHT or w < MIN_WIN_WIDTH:
        msg = "Terminal is too small"
        sub = f"Minimum size required: {MIN_WIN_WIDTH}x{MIN_WIN_HEIGHT} (Current: {w}x{h})"
        _put(win, max(0, h // 2 - 1), max(0, (w - len(msg)) // 2), msg, cp(C_WARN))
        _put(win, max(0, h // 2), max(0, (w - len(sub)) // 2), sub, cp(C_DIM))
        return -1

    y = 0
    for line in LOGO_LINES:
        _put(win, y, 2, line, cp(C_TITLE) | curses.A_BOLD)
        y += 1
    y += 1
    _put(win, y, 2, "=" * (w - 4), cp(C_TITLE))
    y += 2
    if subtitle:
        _put(win, y, 2, subtitle, curses.A_BOLD)
        y += 2
    if hint:
        _draw_hint_line(win, h - 1, hint)
    return y


def draw_signal_meter(win: "curses.window", row: int, lvl: float,
                      trigger_threshold: float, saturated: bool) -> None:
    """Render signal level progress bar widget with dBFS readout and saturation indicator."""
    _, w = win.getmaxyx()
    meter_w = max(1, min(40, w - 30))
    bars = int(
        np.clip(
            (lvl - METER_FLOOR_DBFS) / -METER_FLOOR_DBFS * meter_w,
            0,
            meter_w,
        )
    )
    _put(win, row, 2, "Signal:", cp(C_DIM))
    _put(win, row, 10, "[", cp(C_DIM))
    _put(win, row, 11, "#" * bars, cp(C_BAR))
    _put(
        win,
        row,
        11 + bars,
        "." * (meter_w - bars),
        cp(C_DIM) | curses.A_DIM,
    )
    _put(win, row, 11 + meter_w, "]", cp(C_DIM))
    info = f" {lvl:6.1f} dBFS   thr {trigger_threshold:.0f}"
    _put(win, row, 12 + meter_w, info)
    if saturated:
        _put(
            win,
            row,
            12 + meter_w + len(info) + 1,
            "SAT!",
            cp(C_ERR),
        )


def _content_rows(win: "curses.window", start: int,
                  reserved: int = 2) -> tuple[int, bool]:
    """Rows free for content, and whether the reserved rows below really exist."""
    h, _ = win.getmaxyx()
    room = h - start - reserved
    return (room, True) if room >= 1 else (1, False)


def _tail(log: list[tuple[str, int]], win: "curses.window", start: int,
          reserved: int) -> int:
    """Draw the most recent log lines that fit. Returns the number drawn."""
    avail, _ = _content_rows(win, start, reserved)
    visible = log[-avail:]
    for i, (text, pair) in enumerate(visible):
        _put(win, start + i, 2, text, cp(pair))
    return len(visible)


def _move_cursor(idx: int, key: int, count: int, page: Optional[int] = None) -> int:
    """Cursor movement shared by every list the user moves a selection through."""
    if key == curses.KEY_UP:
        return (idx - 1) % count
    if key == curses.KEY_DOWN:
        return (idx + 1) % count
    if page is None:
        return idx
    if key == curses.KEY_NPAGE:
        return min(count - 1, idx + page)
    if key == curses.KEY_PPAGE:
        return max(0, idx - page)
    if key == curses.KEY_HOME:
        return 0
    if key == curses.KEY_END:
        return count - 1
    return idx


def scroll_view(win: "curses.window", subtitle: str, lines: list[tuple[str, int]],
                hint: str = "Enter/Esc = back",
                exit_keys: tuple[int, ...] = (KEY_ESC,) + KEY_ENTER) -> int:
    """Show scrollable lines until one of exit_keys is pressed. Returns that key."""
    top = 0
    flush_input(win)
    win.nodelay(False)
    while True:
        start = draw_chrome(win, subtitle, hint)
        if start == -1:
            win.refresh()
            time.sleep(0.1)
            continue
        avail, _ = _content_rows(win, start)
        max_top = max(0, len(lines) - avail)
        top = min(top, max_top)
        for i in range(top, min(len(lines), top + avail)):
            text, pair = lines[i]
            _put(win, start + (i - top), 2, text, cp(pair))
        win.refresh()
        k = win.getch()
        if k in exit_keys:
            return k
        elif k == curses.KEY_UP:
            top = max(0, top - 1)
        elif k == curses.KEY_DOWN:
            top = min(max_top, top + 1)
        elif k == curses.KEY_NPAGE:
            top = min(max_top, top + avail)
        elif k == curses.KEY_PPAGE:
            top = max(0, top - avail)


def message_box(win: "curses.window", title: str, lines: list[tuple[str, int]],
                hint: str = "Enter/Esc = back") -> None:
    scroll_view(win, title, lines, hint=hint, exit_keys=(KEY_ESC,) + KEY_ENTER)


def menu_select(win: "curses.window", subtitle: str, items: list[str],
                start_idx: int = 0, hint: Optional[str] = None,
                action_keys: tuple[int, ...] = ()) -> Any:
    """Show a scrolling menu. Returns index, or (index, key) if action_keys is specified, or None on Esc."""
    if not items:
        return (None, KEY_ESC) if action_keys else None
    idx = max(0, min(start_idx, len(items) - 1))
    flush_input(win)
    win.nodelay(False)
    if hint is None:
        hint = "Up/Down = move ▎ Enter = select ▎ Esc = back"
    while True:
        start = draw_chrome(win, subtitle, hint)
        if start == -1:
            win.refresh()
            time.sleep(0.1)
            continue
        _, w = win.getmaxyx()
        avail, _ = _content_rows(win, start)
        top = max(0, idx - avail + 1)
        for i in range(top, min(len(items), top + avail)):
            row = start + (i - top)
            if i == idx:
                _put(win, row, 4, f" > {items[i]} ".ljust(max(0, w - 8)),
                     cp(C_ACCENT))
            else:
                _put(win, row, 4, f"   {items[i]}", cp(C_DIM))
        win.refresh()
        k = win.getch()
        if k in KEY_ENTER:
            return (idx, k) if action_keys else idx
        elif k == KEY_ESC:
            return (None, KEY_ESC) if action_keys else None
        elif action_keys and k in action_keys:
            return (idx, k)
        else:
            idx = _move_cursor(idx, k, len(items), avail)


def confirm_dialog(win: "curses.window", subtitle: str, question: str) -> bool:
    """Show a confirmation box. Returns True on Enter/Y, False on Esc/N."""
    lines = [
        (question, C_WARN),
        ("", C_DIM),
        ("Are you sure you want to proceed?", C_DIM),
    ]
    res = scroll_view(win, subtitle, lines, hint="Enter/Y = Yes ▎ Esc/N = No",
                      exit_keys=(KEY_ESC,) + KEY_ENTER + (ord('y'), ord('Y'), ord('n'), ord('N')))
    return res in KEY_ENTER or res in (ord('y'), ord('Y'))


def edit_text(win: "curses.window", prompt: str, initial: str,
              redraw_fn: Optional[Any] = None) -> Optional[str]:
    """Single-line editor. Returns the text, or None on Esc."""
    buf = list(initial)
    pos = len(buf)
    flush_input(win)
    win.nodelay(False)
    hide_cursor(True)
    try:
        while True:
            h, w = win.getmaxyx()
            if h < MIN_WIN_HEIGHT or w < MIN_WIN_WIDTH:
                win.erase()
                msg = "Terminal is too small"
                sub = f"Minimum size required: {MIN_WIN_WIDTH}x{MIN_WIN_HEIGHT} (Current: {w}x{h})"
                _put(win, max(0, h // 2 - 1), max(0, (w - len(msg)) // 2), msg, cp(C_WARN))
                _put(win, max(0, h // 2), max(0, (w - len(sub)) // 2), sub, cp(C_DIM))
                win.refresh()
                time.sleep(0.1)
                continue

            if redraw_fn:
                redraw_fn()

            row = h - 3
            for r in range(h - 4, h):
                _put(win, r, 0, " " * w)
            _put(win, row - 1, 2, prompt, cp(C_WARN))
            field_w = max(1, w - 5)
            off = max(0, pos - field_w + 1)
            _put(win, row, 2, "> " + "".join(buf[off:off + field_w]))
            _draw_hint_line(win, h - 1, "Enter = confirm ▎ Esc = cancel")
            try:
                win.move(row, 4 + pos - off)
            except curses.error:
                pass
            win.refresh()
            k = win.getch()
            if k in KEY_ENTER:
                return "".join(buf)
            elif k == KEY_ESC:
                return None
            elif k == curses.KEY_RESIZE:
                win.erase()
            elif k in (curses.KEY_BACKSPACE, 127, 8):
                if pos > 0:
                    del buf[pos - 1]
                    pos -= 1
            elif k == curses.KEY_DC:
                if pos < len(buf):
                    del buf[pos]
            elif k == curses.KEY_LEFT:
                pos = max(0, pos - 1)
            elif k == curses.KEY_RIGHT:
                pos = min(len(buf), pos + 1)
            elif k == curses.KEY_HOME:
                pos = 0
            elif k == curses.KEY_END:
                pos = len(buf)
            elif 32 <= k <= 126:
                buf.insert(pos, chr(k))
                pos += 1
    finally:
        hide_cursor(False)


def edit_number(win: "curses.window", prompt: str, initial: Any, cast_type: type,
                min_val: float, max_val: float,
                redraw_fn: Optional[Any] = None) -> Optional[Any]:
    """Prompt until a number within range is entered. Returns None on Esc."""
    text = str(initial)
    while True:
        raw = edit_text(win, f"{prompt}  [{min_val} .. {max_val}]", text, redraw_fn=redraw_fn)
        if raw is None:
            return None
        raw = raw.strip()
        if raw == "":
            return initial
        text = raw
        try:
            val = cast_type(raw)
        except ValueError:
            _flash(win, "Invalid number.", C_ERR)
            continue
        if isinstance(val, float) and not math.isfinite(val):
            _flash(win, "Value must be finite.", C_ERR)
            continue
        if val < min_val:
            _flash(win, f"Must be >= {min_val}", C_ERR)
            continue
        if val > max_val:
            _flash(win, f"Must be <= {max_val}", C_ERR)
            continue
        return val


def _flash(win: "curses.window", msg: str, pair: int) -> None:
    h, w = win.getmaxyx()
    _put(win, h - 3, 2, msg.ljust(w - 4), cp(pair))
    win.refresh()
    time.sleep(FLASH_SECONDS)
    curses.flushinp()
