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
from typing import Any, Optional

from pluto_siege.config import AppConfig, validate_config

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


def _build_setting_rows(cfg: AppConfig) -> list[dict[str, Any]]:
    freq_range = freq_bounds(cfg)
    return [
        {"key": "pluto_uri", "label": "Pluto URI",
         "val": lambda: cfg.pluto_uri,
         "kind": "text"},
        {"key": "permit_out_of_spec_frequency", "label": "Permit out-of-spec freq (70M-6G)",
         "val": lambda: "yes" if cfg.permit_out_of_spec_frequency else "no",
         "kind": "bool"},
        {"key": "rx_freq", "label": "RX frequency (Hz)",
         "val": lambda: f"{cfg.rx_freq}  ({cfg.rx_freq / 1e6:.3f} MHz)",
         "kind": "int", "range": freq_range},
        {"key": "tx_freq", "label": "TX frequency (Hz)",
         "val": lambda: f"{cfg.tx_freq}  ({cfg.tx_freq / 1e6:.3f} MHz)",
         "kind": "int", "range": freq_range},
        {"key": "sample_rate", "label": "Sample rate (Hz)",
         "val": lambda: f"{cfg.sample_rate}  ({cfg.sample_rate / 1e6:.2f} MSPS)",
         "kind": "int", "range": SAMPLE_RATE_RANGE},
        {"key": "rx_gain", "label": "RX gain (dB)",
         "val": lambda: f"{cfg.rx_gain:.2f}",
         "kind": "float", "range": RX_GAIN_RANGE},
        {"key": "tx_gain", "label": "TX gain (dB)",
         "val": lambda: f"{cfg.tx_gain:.2f}",
         "kind": "float", "range": TX_GAIN_RANGE},
        {"key": "rx_buffer_size", "label": "RX buffer size",
         "val": lambda: str(cfg.rx_buffer_size),
         "kind": "int", "range": RX_BUFFER_RANGE},
        {"key": "pre_trigger_buffers", "label": "Pre-trigger buffers",
         "val": lambda: str(cfg.pre_trigger_buffers),
         "kind": "int", "range": PRE_TRIGGER_RANGE},
        {"key": "silence_seconds", "label": "Silence seconds",
         "val": lambda: f"{cfg.silence_seconds:.2f}",
         "kind": "float", "range": SILENCE_RANGE},
        {"key": "max_post_trigger_seconds", "label": "Max post-trigger seconds",
         "val": lambda: f"{cfg.max_post_trigger_seconds:.2f}",
         "kind": "float", "range": POST_TRIGGER_RANGE},
        {"key": "auto_threshold", "label": "Auto noise threshold",
         "val": lambda: "yes" if cfg.auto_threshold else "no",
         "kind": "bool"},
        {"key": "auto_trigger_margin", "label": "Auto trigger margin (dB)",
         "val": lambda: f"{cfg.auto_trigger_margin:.1f}"
                         + ("" if cfg.auto_threshold else "  (unused: auto off)"),
         "kind": "float", "range": AUTO_MARGIN_RANGE},
        {"key": "manual_threshold", "label": "Manual threshold (dBFS)",
         "val": lambda: f"{cfg.manual_threshold:.1f}"
                         + ("" if not cfg.auto_threshold else "  (unused: auto on)"),
         "kind": "float", "range": THRESHOLD_RANGE},
    ]


def _apply_setting(
    row: dict[str, Any], win: "curses.window", cfg: AppConfig, redraw_fn: Any = None
) -> None:
    key, kind = row["key"], row["kind"]
    label = row["label"]
    cur_val = getattr(cfg, key)
    if kind == "text":
        new = edit_text(win, f"{label}  (e.g. ip:192.168.2.1 or usb:)", str(cur_val), redraw_fn=redraw_fn)
        if new is not None and new.strip():
            setattr(cfg, key, new.strip())
    elif kind == "bool":
        cur_str = "yes" if cur_val else "no"
        while True:
            new = edit_text(win, f"{label}  (yes / no)", cur_str, redraw_fn=redraw_fn)
            if new is None:
                break
            val = new.strip().lower()
            if val in ("yes", "y", "true", "1"):
                setattr(cfg, key, True)
                break
            elif val in ("no", "n", "false", "0"):
                setattr(cfg, key, False)
                break
            else:
                _flash(win, "Invalid value. Type \"yes\" or \"no\".", C_ERR)
                cur_str = new.strip()
    else:
        cast = int if kind == "int" else float
        new = edit_number(win, label, cur_val, cast, *row["range"], redraw_fn=redraw_fn)
        if new is not None:
            setattr(cfg, key, cast(new))


def screen_settings(win: "curses.window", config: Optional[AppConfig] = None) -> None:
    cfg = CONFIG if config is None else config
    idx = 0
    win.nodelay(False)
    backup = dict(cfg.to_dict())

    def render_settings_list(current_rows: Optional[list[dict[str, Any]]] = None) -> int:
        rows = _build_setting_rows(cfg) if current_rows is None else current_rows
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
            row = start + (i - top)
            r = rows[i]
            prefix = " > " if i == idx else "   "
            val = r["val"]()
            val_str = f" {val} " if i == idx else val
            lbl = r["label"]
            line_str = f"{prefix}{lbl}".ljust(val_col) + val_str
            pair = cp(C_ACCENT) if i == idx else cp(C_DIM)
            _put(win, row, 2, line_str.ljust(max(0, w - 4)), pair)
        return start

    def handle_common_key(k: int) -> bool:
        if k in (ord("s"), ord("S")):
            if save_settings(cfg):
                backup.clear()
                backup.update(cfg.to_dict())
                _flash(win, "Settings saved.", C_OK)
            else:
                _flash(win, "Could not write settings file!", C_ERR)
            return True
        elif k in (ord("r"), ord("R")):
            cfg.update(DEFAULT_SETTINGS)
            _flash(win, "Defaults restored.", C_OK)
            return True
        return False

    while True:
        rows = _build_setting_rows(cfg)
        start = render_settings_list(rows)
        if start == -1:
            win.refresh()
            k = win.getch()
            if k == KEY_ESC:
                cfg.update(backup)
                return
            continue

        avail, _ = _content_rows(win, start)
        win.refresh()
        k = win.getch()
        if handle_common_key(k):
            continue

        if k in KEY_ENTER:
            _apply_setting(rows[idx], win, cfg, redraw_fn=render_settings_list)
            flush_input(win)
            before = dict(cfg.to_dict())
            validated = validate_config(before).to_dict()
            cfg.update(validated)
            reset = [key for key, val in validated.items() if before[key] != val]
            if reset:
                _flash(win, "Out of range, reset to default: " + ", ".join(reset), C_WARN)
        elif k == KEY_ESC:
            cfg.update(backup)
            return
        else:
            idx = _move_cursor(idx, k, len(rows), avail)
