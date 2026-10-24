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

from __future__ import annotations

import curses
import math
import os
import time
from dataclasses import dataclass
from typing import Any, overload

from pluto_siege.constants import LOGO_LINES

os.environ.setdefault("ESCDELAY", "50")

# ---------------------------------------------------------------------------
# Colour pairs & layout constants
# ---------------------------------------------------------------------------

C_TITLE = 1
C_ACCENT = 2
C_OK = 3
C_WARN = 4
C_ERR = 5
C_DIM = 6
C_BAR = C_OK

CONTENT_X = 2
MENU_X = 4
RIGHT_INSET = 4
FOOTER_ROW_OFFSET = 1
MIN_WIN_HEIGHT = 37
MIN_WIN_WIDTH = 97

FLASH_SECONDS = 0.9
METER_REFRESH_SECONDS = 0.1
METER_FLOOR_DBFS = -80.0
KEY_ENTER = (
    10,
    13,
    curses.KEY_ENTER,
    getattr(curses, "PADENTER", 459),
    getattr(curses, "CTL_ENTER", 529),
)
KEY_ESC = 27
ESC_DELAY_MS = 50

BUTTON_CLICK = getattr(curses, "BUTTON1_CLICKED", 0) | getattr(
    curses, "BUTTON1_PRESSED", 0
)
BUTTON_DOUBLE = getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
BUTTON_RIGHT = getattr(curses, "BUTTON3_CLICKED", 0) | getattr(
    curses, "BUTTON3_PRESSED", 0
)
WHEEL_UP = getattr(curses, "BUTTON4_PRESSED", 0) | getattr(curses, "BUTTON4_CLICKED", 0)
WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0) | getattr(
    curses, "BUTTON5_CLICKED", 0
)


@dataclass(frozen=True)
class HitBox:
    """Inclusive-left/exclusive-right cell rectangle used for mouse targets."""

    y: int
    x: int
    height: int
    width: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def init_mouse(enable: bool = True) -> None:
    """Enable or disable curses mouse tracking."""
    if not hasattr(curses, "mousemask"):
        return
    try:
        mask = getattr(curses, "ALL_MOUSE_EVENTS", 0) if enable else 0
        curses.mousemask(mask)
    except curses.error:
        pass


def get_mouse_event() -> tuple[int, int, int, int, int] | None:
    """Safely fetch curses mouse event tuple (id, x, y, z, bstate) or None."""
    if not hasattr(curses, "getmouse"):
        return None
    try:
        return curses.getmouse()
    except (curses.error, OSError, ValueError):
        return None


def init_colors() -> None:
    if not curses.has_colors():
        return
    try:
        curses.start_color()
    except curses.error:
        pass
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
    except curses.error:
        pass


def cp(pair: int) -> int:
    try:
        if curses.has_colors():
            return curses.color_pair(pair)
    except curses.error:
        pass
    return curses.A_REVERSE if pair == C_ACCENT else 0


def set_cursor_visible(visible: bool) -> None:
    """Show or hide the caret. Terminals may refuse, which is not an error."""
    try:
        curses.curs_set(1 if visible else 0)
    except curses.error:
        pass


hide_cursor = set_cursor_visible


def flush_input(win: curses.window | None = None) -> None:
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
        except (curses.error, AttributeError):
            pass
    try:
        curses.flushinp()
    except curses.error:
        pass


def _sanitize_text(text: str) -> str:
    """Replace control characters with spaces so labels cannot inject escapes."""
    return "".join(ch if ch.isprintable() else " " for ch in str(text))


def _put(win: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    """Draw text clipped to the window, reserving the bottom-right terminal cell."""
    try:
        h, w = win.getmaxyx()
    except (curses.error, AttributeError, TypeError, ValueError):
        return
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    clipped = _sanitize_text(text)[: max(0, w - 1 - x)]
    if not clipped:
        return
    try:
        win.addstr(y, x, clipped, attr)
    except curses.error:
        pass


def _draw_hint_line(win: curses.window, y: int, hint: str) -> None:
    """Draw footer hint line with bold cyan '▎' separators and dim white text."""
    hint_str = hint.strip()
    if not hint_str:
        return
    full_hint = f"▎ {hint_str}"
    parts = full_hint.split("▎")
    curr_x = CONTENT_X
    for i, part in enumerate(parts):
        if part:
            _put(win, y, curr_x, part, cp(C_DIM) | curses.A_DIM)
            curr_x += len(part)
        if i < len(parts) - 1:
            _put(win, y, curr_x, "▎", cp(C_TITLE) | curses.A_BOLD)
            curr_x += 1


def draw_footer(win: curses.window, hint: str) -> None:
    """Draw unified keyboard hint in the bottom row."""
    if not hint:
        return
    try:
        h, _ = win.getmaxyx()
    except (curses.error, AttributeError, TypeError, ValueError):
        return
    _draw_hint_line(win, h - FOOTER_ROW_OFFSET, hint)


def draw_action_button(
    win: curses.window,
    row: int,
    x: int,
    label: str,
    selected: bool = False,
    accent: int = C_ACCENT,
) -> HitBox:
    """Draw one explicit action button and return its clickable HitBox."""
    text = f"[ {_sanitize_text(label)} ]"
    try:
        height, width = win.getmaxyx()
    except (curses.error, AttributeError, TypeError, ValueError):
        return HitBox(row, x, 0, 0)
    visible_width = min(len(text), max(0, width - 1 - x))
    visible_height = 1 if visible_width and 0 <= row < height and 0 <= x < width else 0
    if visible_height:
        _put(win, row, x, text, cp(accent) if selected else cp(C_DIM))
    return HitBox(row, x, visible_height, visible_width if visible_height else 0)


def draw_list_row(win: curses.window, row: int, label: str, selected: bool) -> HitBox:
    """Draw a menu or settings row with a consistent, clickable full-width bar."""
    try:
        height, width = win.getmaxyx()
    except (curses.error, AttributeError, TypeError, ValueError):
        return HitBox(row, MENU_X, 0, 0)
    field_width = max(0, width - MENU_X - RIGHT_INSET)
    visible_width = min(field_width, max(0, width - 1 - MENU_X))
    if visible_width <= 0 or not (0 <= row < height):
        return HitBox(row, MENU_X, 0, 0)
    prefix = " > " if selected else "   "
    _put(
        win,
        row,
        MENU_X,
        f"{prefix}{label}".ljust(visible_width),
        cp(C_ACCENT) if selected else cp(C_DIM),
    )
    return HitBox(row, MENU_X, 1, visible_width)


def update_term_size(win: curses.window | None = None) -> None:
    """Synchronize curses screen dimensions when the terminal resizes."""
    try:
        if hasattr(curses, "update_lines_cols"):
            curses.update_lines_cols()
    except curses.error:
        pass


def screen_is_usable(win: curses.window) -> bool:
    """Return True if the terminal window meets minimum 97x37 dimensions."""
    try:
        update_term_size(win)
        h, w = win.getmaxyx()
        return h >= MIN_WIN_HEIGHT and w >= MIN_WIN_WIDTH
    except (curses.error, AttributeError, TypeError, ValueError):
        return False


def wait_for_usable_screen(win: curses.window) -> bool:
    """Wait until the terminal meets minimum dimensions; Esc/Q cancels."""
    while draw_chrome(win, "", "Esc/Q = cancel") == -1:
        try:
            win.refresh()
            if win.getch() in (KEY_ESC, ord("q"), ord("Q")):
                return False
        except curses.error:
            return False
    return True


def draw_chrome(win: curses.window, subtitle: str = "", hint: str = "") -> int:
    """Draw logo, divider, subtitle, and footer hint. Returns first free content row or -1."""
    update_term_size(win)
    try:
        win.keypad(True)
    except curses.error:
        pass
    win.erase()
    h, w = win.getmaxyx()
    if h < MIN_WIN_HEIGHT or w < MIN_WIN_WIDTH:
        msg = "Terminal is too small"
        sub = f"Minimum size required: {MIN_WIN_WIDTH}x{MIN_WIN_HEIGHT} (Current: {w}x{h})"
        _put(win, max(0, h // 2 - 1), max(0, (w - len(msg)) // 2), msg, cp(C_WARN))
        _put(win, max(0, h // 2), max(0, (w - len(sub)) // 2), sub, cp(C_DIM))
        if hint and h >= 3:
            draw_footer(win, hint)
        return -1

    y = 0
    for line in LOGO_LINES:
        _put(win, y, CONTENT_X, line, cp(C_TITLE) | curses.A_BOLD)
        y += 1
    y += 1
    _put(win, y, CONTENT_X, "=" * (w - 4), cp(C_TITLE))
    y += 2
    if subtitle:
        _put(win, y, CONTENT_X, subtitle, cp(C_DIM) | curses.A_BOLD)
        y += 2
    if hint:
        draw_footer(win, hint)
    return y


def draw_signal_meter(
    win: curses.window, row: int, lvl: float, trigger_threshold: float, saturated: bool
) -> None:
    """Render signal level progress bar widget with dBFS readout and saturation indicator."""
    try:
        h, w = win.getmaxyx()
        level = float(lvl)
        threshold = float(trigger_threshold)
        if not isinstance(h, int) or not isinstance(w, int):
            return
    except (ValueError, TypeError, curses.error, AttributeError):
        return
    if not math.isfinite(level) or not math.isfinite(threshold):
        return

    meter_w = max(1, min(40, w - 30))
    bars = max(
        0,
        min(
            meter_w,
            int((level - METER_FLOOR_DBFS) / -METER_FLOOR_DBFS * meter_w),
        ),
    )
    _put(win, row, CONTENT_X, "Signal:", cp(C_DIM))
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
    info = f" {level:6.1f} dBFS   thr {threshold:.0f}"
    _put(win, row, 12 + meter_w, info, cp(C_DIM))
    sat_x = 12 + meter_w + len(info) + 1
    if saturated:
        _put(win, row, sat_x, "SAT!", cp(C_ERR) | curses.A_BOLD)
    else:
        _put(win, row, sat_x, "    ", 0)


def draw_gps_status(
    win: curses.window, row: int, status_text: str, has_fix: bool = False
) -> None:
    """Render live GPS status line with colored status tags."""
    try:
        h, w = win.getmaxyx()
        if not isinstance(h, int) or not isinstance(w, int):
            return
    except (ValueError, TypeError, curses.error, AttributeError):
        return
    if row >= h - 1:
        return
    lowered = status_text.lower()
    if has_fix:
        color = cp(C_OK) | curses.A_BOLD
        tag = "[FIX] "
    elif any(
        err in lowered
        for err in ("cannot", "lost", "invalid", "permission", "missing", "error")
    ):
        color = cp(C_ERR)
        tag = "[ERR] "
    elif "searching" in lowered:
        color = cp(C_WARN)
        tag = "[NO FIX] "
    elif any(wait in lowered for wait in ("connecting", "waiting")):
        color = cp(C_WARN)
        tag = "[WAIT] "
    else:
        color = cp(C_DIM)
        tag = ""

    label = "GPS:    "
    _put(win, row, CONTENT_X, label, cp(C_DIM))
    avail_w = max(0, w - 4 - len(label))
    msg = f"{tag}{status_text}"
    _put(win, row, CONTENT_X + len(label), msg[:avail_w].ljust(avail_w), color)


def _content_rows(
    win: curses.window, start: int, reserved: int = 2
) -> tuple[int, bool]:
    """Rows free for content, and whether the reserved rows below exist."""
    if start < 0:
        return (0, False)
    h, _ = win.getmaxyx()
    room = h - start - reserved
    return (room, True) if room >= 1 else (1, False)


def _tail(
    log: list[tuple[str, int]], win: curses.window, start: int, reserved: int
) -> int:
    """Draw the most recent log lines that fit. Returns the number drawn."""
    avail, _ = _content_rows(win, start, reserved)
    visible = log[-avail:] if avail > 0 else []
    for i, (text, pair) in enumerate(visible):
        _put(win, start + i, CONTENT_X, text, cp(pair))
    return len(visible)


def _move_cursor(idx: int, key: int, count: int, page: int | None = None) -> int:
    """Cursor movement shared by every list the user navigates."""
    if count <= 0:
        return 0
    if key in (curses.KEY_UP, ord("k"), ord("K")):
        return (idx - 1) % count
    if key in (curses.KEY_DOWN, ord("j"), ord("J")):
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


def scroll_view(
    win: curses.window,
    subtitle: str,
    lines: list[tuple[str, int]],
    hint: str = "Enter/Esc = back",
    exit_keys: tuple[int, ...] = (KEY_ESC, ord("q"), ord("Q")) + KEY_ENTER,
) -> int:
    """Show scrollable lines until one of exit_keys is pressed. Returns that key."""
    flat_lines: list[tuple[str, int]] = []
    for text, pair in lines:
        for sub in str(text).splitlines():
            flat_lines.append((sub, pair))
    lines = flat_lines or [("", 0)]
    top = 0
    flush_input(win)
    win.nodelay(False)
    while True:
        start = draw_chrome(win, subtitle, hint)
        if start == -1:
            win.refresh()
            k = win.getch()
            if k in exit_keys or k in (KEY_ESC, ord("q"), ord("Q")):
                return k
            continue
        avail, _ = _content_rows(win, start)
        max_top = max(0, len(lines) - avail)
        top = min(top, max_top)
        for i in range(top, min(len(lines), top + avail)):
            text, pair = lines[i]
            _put(win, start + (i - top), CONTENT_X, text, cp(pair))
        win.refresh()
        k = win.getch()
        if k in exit_keys:
            return k
        elif k == getattr(curses, "KEY_RESIZE", -1):
            continue
        elif k in (curses.KEY_UP, ord("k"), ord("K")):
            top = max(0, top - 1)
        elif k in (curses.KEY_DOWN, ord("j"), ord("J")):
            top = min(max_top, top + 1)
        elif k == curses.KEY_NPAGE:
            top = min(max_top, top + avail)
        elif k == curses.KEY_PPAGE:
            top = max(0, top - avail)
        elif k == curses.KEY_HOME:
            top = 0
        elif k == curses.KEY_END:
            top = max_top
        elif k == getattr(curses, "KEY_MOUSE", -1):
            m = get_mouse_event()
            if m is not None:
                _mid, _mx, _my, _mz, bstate = m
                if bstate & WHEEL_UP:
                    top = max(0, top - 3)
                elif bstate & WHEEL_DOWN:
                    top = min(max_top, top + 3)
                elif bstate & BUTTON_RIGHT:
                    if KEY_ESC in exit_keys:
                        return KEY_ESC
                    return exit_keys[0] if exit_keys else KEY_ESC


def message_box(
    win: curses.window,
    title: str,
    lines: list[tuple[str, int]],
    hint: str = "Enter/Esc/Q = back",
) -> None:
    scroll_view(
        win,
        title,
        lines,
        hint=hint,
        exit_keys=(KEY_ESC, ord("q"), ord("Q")) + KEY_ENTER,
    )


@overload
def menu_select(
    win: curses.window,
    subtitle: str,
    items: list[str],
    start_idx: int = 0,
    hint: str | None = None,
    action_keys: tuple[()] = (),
    hotkeys: bool = False,
) -> int | None: ...


@overload
def menu_select(
    win: curses.window,
    subtitle: str,
    items: list[str],
    start_idx: int = 0,
    hint: str | None = None,
    action_keys: tuple[int, ...] = ...,
    hotkeys: bool = False,
) -> tuple[int | None, int]: ...


def menu_select(
    win: curses.window,
    subtitle: str,
    items: list[str],
    start_idx: int = 0,
    hint: str | None = None,
    action_keys: tuple[int, ...] = (),
    hotkeys: bool = False,
) -> int | None | tuple[int | None, int]:
    """Show a scrolling menu. Returns index, or (index, key) if action_keys is specified, or None on Esc."""
    if not items:
        return (None, KEY_ESC) if action_keys else None
    idx = max(0, min(start_idx, len(items) - 1))
    top = 0
    flush_input(win)
    win.nodelay(False)
    if hint is None:
        hint = "Up/Down = move ▎ Enter = select ▎ Esc/Q = back"
    while True:
        start = draw_chrome(win, subtitle, hint)
        if start == -1:
            win.refresh()
            k = win.getch()
            if k in (KEY_ESC, ord("q"), ord("Q")):
                return (None, KEY_ESC) if action_keys else None
            continue
        avail, _ = _content_rows(win, start)
        if idx < top:
            top = idx
        elif idx >= top + avail:
            top = idx - avail + 1
        top = max(0, min(top, max(0, len(items) - avail)))
        hitboxes: dict[int, HitBox] = {}
        for i in range(top, min(len(items), top + avail)):
            row = start + (i - top)
            hitboxes[i] = draw_list_row(win, row, items[i], i == idx)
        win.refresh()
        k = win.getch()
        if k in KEY_ENTER:
            return (idx, k) if action_keys else idx
        elif k in (KEY_ESC, ord("q"), ord("Q")):
            return (None, KEY_ESC) if action_keys else None
        elif action_keys and k in action_keys:
            return (idx, k)
        elif hotkeys and ord("1") <= k <= ord("9"):
            num_idx = k - ord("1")
            if 0 <= num_idx < len(items):
                return (num_idx, KEY_ENTER[0]) if action_keys else num_idx
        elif hotkeys and 32 <= k <= 126 and chr(k).isalnum():
            ch = chr(k).upper()
            for item_idx, item in enumerate(items):
                clean_item = item.strip()
                if clean_item and clean_item[0].upper() == ch:
                    return (item_idx, KEY_ENTER[0]) if action_keys else item_idx
        elif k == getattr(curses, "KEY_RESIZE", -1):
            continue
        elif k == getattr(curses, "KEY_MOUSE", -1):
            m = get_mouse_event()
            if m is not None:
                _mid, mx, my, _mz, bstate = m
                if bstate & WHEEL_UP:
                    idx = max(0, idx - 1)
                elif bstate & WHEEL_DOWN:
                    idx = min(len(items) - 1, idx + 1)
                elif bstate & BUTTON_RIGHT:
                    return (None, KEY_ESC) if action_keys else None
                elif bstate & (BUTTON_CLICK | BUTTON_DOUBLE):
                    clicked_idx = top + (my - start)
                    hitbox = hitboxes.get(clicked_idx)
                    if hitbox is not None and hitbox.contains(mx, my):
                        if clicked_idx == idx or (bstate & BUTTON_DOUBLE):
                            return (
                                (clicked_idx, KEY_ENTER[0])
                                if action_keys
                                else clicked_idx
                            )
                        idx = clicked_idx
        else:
            idx = _move_cursor(idx, k, len(items), avail)


def confirm_dialog(win: curses.window, subtitle: str, question: str) -> bool:
    """Ask a confirmation question with keyboard and mouse button support."""
    selected_yes = True
    flush_input(win)
    win.nodelay(False)
    while True:
        start = draw_chrome(
            win,
            subtitle,
            "Left/Right/Space = choose ▎ Enter = confirm ▎ Y = yes ▎ N/Esc = no",
        )
        if start == -1:
            win.refresh()
            key = win.getch()
            if key in (KEY_ESC, ord("n"), ord("N")):
                return False
            continue
        _put(win, start, CONTENT_X, question, cp(C_WARN))
        _put(win, start + 2, CONTENT_X, "Are you sure you want to proceed?", cp(C_DIM))
        yes = draw_action_button(win, start + 4, CONTENT_X, "Y  Yes", selected_yes)
        no = draw_action_button(
            win, start + 4, CONTENT_X + yes.width + 2, "N  No", not selected_yes
        )
        win.refresh()
        key = win.getch()
        if key in KEY_ENTER:
            return selected_yes
        if key in (ord("y"), ord("Y")):
            return True
        if key in (KEY_ESC, ord("n"), ord("N")):
            return False
        if key in (curses.KEY_LEFT, ord("h"), curses.KEY_RIGHT, ord("l"), ord(" ")):
            selected_yes = not selected_yes
        elif key == getattr(curses, "KEY_RESIZE", -1):
            continue
        elif key == getattr(curses, "KEY_MOUSE", -1):
            event = get_mouse_event()
            if event is not None:
                _mid, mx, my, _mz, state = event
                if state & BUTTON_RIGHT:
                    return False
                if state & (BUTTON_CLICK | BUTTON_DOUBLE):
                    if yes.contains(mx, my):
                        return True
                    if no.contains(mx, my):
                        return False


def edit_text(
    win: curses.window, prompt: str, initial: str, redraw_fn: Any | None = None
) -> str | None:
    """Single-line editor. Returns the edited text, or None on Esc."""
    buf = list(initial)
    pos = len(buf)
    flush_input(win)
    win.nodelay(False)
    set_cursor_visible(True)
    try:
        while True:
            if not screen_is_usable(win):
                draw_chrome(win, "", "Esc = cancel")
                win.refresh()
                k = win.getch()
                if k == KEY_ESC:
                    return None
                continue

            if redraw_fn:
                redraw_fn()

            h, w = win.getmaxyx()
            row = h - 3
            for r in range(h - 4, h):
                _put(win, r, 0, " " * w)
            _put(win, row - 1, CONTENT_X, prompt, cp(C_WARN))
            field_w = max(1, w - 5)
            off = max(0, pos - field_w + 1)
            _put(win, row, CONTENT_X, "> " + "".join(buf[off : off + field_w]))
            draw_footer(win, "Enter = confirm ▎ Esc = cancel")
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
                update_term_size(win)
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
            elif k == getattr(curses, "KEY_MOUSE", -1):
                m = get_mouse_event()
                if m is not None:
                    _mid, mx, my, _mz, bstate = m
                    if bstate & BUTTON_RIGHT:
                        return None
                    elif (
                        bstate & (BUTTON_CLICK | BUTTON_DOUBLE)
                        and my == row
                        and mx >= 4
                    ):
                        pos = max(0, min(len(buf), off + (mx - 4)))
    finally:
        set_cursor_visible(False)


def parse_number(
    raw: str, cast_type: type, min_val: float, max_val: float
) -> int | float:
    """Parse numeric input string with unit multipliers (k, M, G, Hz, SPS).

    Auto-converts decimal MHz (e.g. 433.92) to Hz when cast_type is int and min_val >= 100_000.
    """
    cleaned = raw.strip().lower()
    mult = 1.0
    explicit_unit = False
    for suffix, factor in (
        ("ghz", 1e9),
        ("g", 1e9),
        ("msps", 1e6),
        ("mhz", 1e6),
        ("m", 1e6),
        ("khz", 1e3),
        ("k", 1e3),
        ("sps", 1.0),
        ("hz", 1.0),
    ):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            mult = factor
            explicit_unit = True
            break

    try:
        num = float(cleaned)
    except (ValueError, OverflowError):
        raise ValueError("Invalid number.") from None

    if not math.isfinite(num):
        raise ValueError("Value must be finite.")

    num *= mult
    if not math.isfinite(num):
        raise ValueError("Value must be finite.")

    if (
        cast_type is int
        and not explicit_unit
        and min_val >= 100_000
        and 0 < num < 10_000
    ):
        num *= 1e6
        if not math.isfinite(num):
            raise ValueError("Value must be finite.")

    if cast_type is int:
        rounded = round(num)
        if abs(num - rounded) > 1e-3:
            raise ValueError("Value must be an integer.")
        return int(rounded)
    return num


def edit_number(
    win: curses.window,
    prompt: str,
    initial: Any,
    cast_type: type,
    min_val: float,
    max_val: float,
    redraw_fn: Any | None = None,
) -> Any | None:
    """Prompt until a number within range is entered. Returns None on Esc."""
    text = str(initial)
    while True:
        raw = edit_text(
            win, f"{prompt}  [{min_val} .. {max_val}]", text, redraw_fn=redraw_fn
        )
        if raw is None:
            return None
        raw = raw.strip()
        if raw == "":
            return initial
        text = raw
        try:
            val = parse_number(raw, cast_type, min_val, max_val)
        except ValueError as err:
            _flash(win, str(err) if str(err) else "Invalid number.", C_ERR)
            continue
        except OverflowError:
            _flash(win, "Invalid number.", C_ERR)
            continue
        if val < min_val:
            _flash(win, f"Must be >= {min_val}", C_ERR)
            continue
        if val > max_val:
            _flash(win, f"Must be <= {max_val}", C_ERR)
            continue
        return val


def _flash(win: curses.window, msg: str, pair: int) -> None:
    h, w = win.getmaxyx()
    _put(win, h - 3, CONTENT_X, msg.ljust(max(0, w - 4)), cp(pair))
    win.refresh()
    time.sleep(FLASH_SECONDS)
    flush_input(win)
