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

"""Settings screen controller."""

import curses
import time
from typing import Any

from pluto_siege.constants import (
    AUTO_MARGIN_RANGE,
    POST_TRIGGER_RANGE,
    PRE_TRIGGER_RANGE,
    RX_BUFFER_RANGE,
    RX_GAIN_RANGE,
    SAMPLE_RATE_RANGE,
    SILENCE_RANGE,
    THRESHOLD_RANGE,
    TX_GAIN_RANGE,
)
from pluto_siege.settings import (
    DEFAULT_SETTINGS,
    CONFIG,
    freq_bounds,
    save_settings,
    validate_settings,
)
from pluto_siege.ui.widgets.framework import (
    C_ACCENT,
    C_DIM,
    C_ERR,
    C_OK,
    C_WARN,
    KEY_ENTER,
    KEY_ESC,
    _content_rows,
    _flash,
    _move_cursor,
    _put,
    cp,
    draw_chrome,
    edit_number,
    edit_text,
    flush_input,
)


def _build_setting_rows() -> list[dict[str, Any]]:
    freq_range = freq_bounds()
    return [
        {"key": "pluto_uri", "label": "Pluto URI",
         "val": lambda: CONFIG.pluto_uri,
         "kind": "text"},
        {"key": "permit_out_of_spec_frequency", "label": "Permit out-of-spec freq (70M-6G)",
         "val": lambda: "yes" if CONFIG.permit_out_of_spec_frequency else "no",
         "kind": "bool"},
        {"key": "rx_freq", "label": "RX frequency (Hz)",
         "val": lambda: f"{CONFIG.rx_freq}  ({CONFIG.rx_freq / 1e6:.3f} MHz)",
         "kind": "int", "range": freq_range},
        {"key": "tx_freq", "label": "TX frequency (Hz)",
         "val": lambda: f"{CONFIG.tx_freq}  ({CONFIG.tx_freq / 1e6:.3f} MHz)",
         "kind": "int", "range": freq_range},
        {"key": "sample_rate", "label": "Sample rate (Hz)",
         "val": lambda: f"{CONFIG.sample_rate}  ({CONFIG.sample_rate / 1e6:.2f} MSPS)",
         "kind": "int", "range": SAMPLE_RATE_RANGE},
        {"key": "rx_gain", "label": "RX gain (dB)",
         "val": lambda: f"{CONFIG.rx_gain:.2f}",
         "kind": "float", "range": RX_GAIN_RANGE},
        {"key": "tx_gain", "label": "TX gain (dB)",
         "val": lambda: f"{CONFIG.tx_gain:.2f}",
         "kind": "float", "range": TX_GAIN_RANGE},
        {"key": "rx_buffer_size", "label": "RX buffer size",
         "val": lambda: str(CONFIG.rx_buffer_size),
         "kind": "int", "range": RX_BUFFER_RANGE},
        {"key": "pre_trigger_buffers", "label": "Pre-trigger buffers",
         "val": lambda: str(CONFIG.pre_trigger_buffers),
         "kind": "int", "range": PRE_TRIGGER_RANGE},
        {"key": "silence_seconds", "label": "Silence seconds",
         "val": lambda: f"{CONFIG.silence_seconds:.2f}",
         "kind": "float", "range": SILENCE_RANGE},
        {"key": "max_post_trigger_seconds", "label": "Max post-trigger seconds",
         "val": lambda: f"{CONFIG.max_post_trigger_seconds:.2f}",
         "kind": "float", "range": POST_TRIGGER_RANGE},
        {"key": "auto_threshold", "label": "Auto noise threshold",
         "val": lambda: "yes" if CONFIG.auto_threshold else "no",
         "kind": "bool"},
        {"key": "auto_trigger_margin", "label": "Auto trigger margin (dB)",
         "val": lambda: f"{CONFIG.auto_trigger_margin:.1f}"
                        + ("" if CONFIG.auto_threshold else "  (unused: auto off)"),
         "kind": "float", "range": AUTO_MARGIN_RANGE},
        {"key": "manual_threshold", "label": "Manual threshold (dBFS)",
         "val": lambda: f"{CONFIG.manual_threshold:.1f}"
                        + ("" if not CONFIG.auto_threshold else "  (unused: auto on)"),
         "kind": "float", "range": THRESHOLD_RANGE},
    ]


def _apply_setting(row: dict[str, Any], win: "curses.window", redraw_fn: Any = None) -> None:
    key, kind = row["key"], row["kind"]
    if kind == "text":
        new = edit_text(win, f"{row['label']}  (e.g. ip:192.168.2.1 or usb:)", CONFIG[key], redraw_fn=redraw_fn)
        if new is not None and new.strip():
            CONFIG[key] = new.strip()
    elif kind == "bool":
        cur_val = "yes" if CONFIG[key] else "no"
        new = edit_text(win, f"{row['label']}  (yes / no)", cur_val, redraw_fn=redraw_fn)
        if new is not None:
            val = new.strip().lower()
            if val == "yes":
                CONFIG[key] = True
            elif val == "no":
                CONFIG[key] = False
            else:
                _flash(win, "Invalid value. Type 'yes' or 'no'.", C_ERR)
    else:
        cast = int if kind == "int" else float
        new = edit_number(win, row["label"], CONFIG[key], cast, *row["range"], redraw_fn=redraw_fn)
        if new is not None:
            CONFIG[key] = cast(new)


def screen_settings(win: "curses.window") -> None:
    idx = 0
    label_w = 34
    flush_input(win)
    win.nodelay(False)
    backup = dict(CONFIG.to_dict())

    def render_settings_list() -> int:
        rows = _build_setting_rows()
        start = draw_chrome(
            win, "Settings",
            "Up/Down = move ▎ Enter = edit ▎ S = save ▎ R = reset (no save) ▎ Esc = back (no save)",
        )
        if start == -1:
            return -1
        _, w = win.getmaxyx()
        avail, _ = _content_rows(win, start)
        top = max(0, idx - avail + 1)
        for i in range(top, min(len(rows), top + avail)):
            row_y = start + (i - top)
            label = rows[i]["label"].ljust(label_w)
            value = rows[i]["val"]()
            if i == idx:
                _put(win, row_y, 4, f" > {label} : {value}".ljust(max(0, w - 8)),
                     cp(C_ACCENT))
            else:
                _put(win, row_y, 4, f"   {label} : ", cp(C_DIM))
                _put(win, row_y, 4 + 3 + label_w + 3, value, cp(C_OK))
        return start

    def handle_common_key(k: int) -> bool:
        if k in (ord('s'), ord('S')):
            if save_settings():
                backup.clear()
                backup.update(CONFIG.to_dict())
                _flash(win, "Settings saved.", C_OK)
            else:
                _flash(win, "Could not write settings file!", C_ERR)
            return True
        elif k in (ord('r'), ord('R')):
            CONFIG.update(DEFAULT_SETTINGS)
            _flash(win, "Defaults restored.", C_OK)
            return True
        return False

    while True:
        rows = _build_setting_rows()
        start = render_settings_list()
        if start == -1:
            win.refresh()
            time.sleep(0.1)
            continue

        win.refresh()
        k = win.getch()
        if handle_common_key(k):
            continue

        if k in KEY_ENTER:
            _apply_setting(rows[idx], win, redraw_fn=render_settings_list)
            flush_input(win)
            before = dict(CONFIG.to_dict())
            CONFIG.update(validate_settings(CONFIG.to_dict()))
            reset = [key for key, val in CONFIG.to_dict().items() if before[key] != val]
            if reset:
                _flash(win, "Out of range, reset to default: " + ", ".join(reset), C_WARN)
        elif k == KEY_ESC:
            CONFIG.update(backup)
            return
        else:
            idx = _move_cursor(idx, k, len(rows))
