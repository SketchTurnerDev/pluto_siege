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

from __future__ import annotations

import curses
from typing import Any

from pluto_siege.config import (
    CONFIG,
    DEFAULT_SETTINGS,
    AppConfig,
    freq_bounds,
    save_settings,
    validate_config,
)
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
from pluto_siege.gps import init_gps
from pluto_siege.sound import SoundCue, play_cue
from pluto_siege.ui.widgets.framework import (
    BUTTON_CLICK,
    BUTTON_DOUBLE,
    BUTTON_RIGHT,
    C_ERR,
    C_OK,
    C_WARN,
    KEY_ENTER,
    KEY_ESC,
    WHEEL_DOWN,
    WHEEL_UP,
    _content_rows,
    _flash,
    _move_cursor,
    confirm_dialog,
    draw_chrome,
    draw_list_row,
    edit_number,
    edit_text,
    flush_input,
    get_mouse_event,
    init_mouse,
)

SETTINGS_HINT = (
    "Up/Down = move ▎ Left/Right = adjust ▎ Enter = edit ▎ "
    "S = save ▎ R = reset ▎ Esc/Q = back"
)


def _build_setting_rows(cfg: AppConfig) -> list[dict[str, Any]]:
    freq_range = freq_bounds(cfg)
    return [
        # --- Radio & Connection ---
        {
            "key": "connection_type",
            "label": "Connection type",
            "val": lambda: "ip" if cfg.pluto_uri.startswith("ip:") else "usb",
            "kind": "enum",
        },
        {
            "key": "pluto_ip",
            "label": "Pluto IP address",
            "val": lambda: (
                f"{cfg.pluto_ip}"
                + ("" if cfg.pluto_uri.startswith("ip:") else "  (unused: USB mode)")
            ),
            "kind": "text",
        },
        {
            "key": "rx_freq",
            "label": "RX frequency (Hz)",
            "val": lambda: f"{cfg.rx_freq}  ({cfg.rx_freq / 1e6:.3f} MHz)",
            "kind": "int",
            "range": freq_range,
        },
        {
            "key": "tx_freq",
            "label": "TX frequency (Hz)",
            "val": lambda: f"{cfg.tx_freq}  ({cfg.tx_freq / 1e6:.3f} MHz)",
            "kind": "int",
            "range": freq_range,
        },
        {
            "key": "permit_out_of_spec_frequency",
            "label": "Extended freq (70MHz-6GHz)",
            "val": lambda: "yes" if cfg.permit_out_of_spec_frequency else "no",
            "kind": "bool",
        },
        {
            "key": "sample_rate",
            "label": "Sample rate (Hz)",
            "val": lambda: f"{cfg.sample_rate}  ({cfg.sample_rate / 1e6:.2f} MSPS)",
            "kind": "int",
            "range": SAMPLE_RATE_RANGE,
        },
        {
            "key": "rx_gain",
            "label": "RX gain (dB)",
            "val": lambda: f"{cfg.rx_gain:.2f}",
            "kind": "float",
            "range": RX_GAIN_RANGE,
        },
        {
            "key": "tx_gain",
            "label": "TX gain (dB)",
            "val": lambda: f"{cfg.tx_gain:.2f}",
            "kind": "float",
            "range": TX_GAIN_RANGE,
        },
        # --- Capture Engine & Trigger ---
        {
            "key": "auto_threshold",
            "label": "Auto noise threshold",
            "val": lambda: "yes" if cfg.auto_threshold else "no",
            "kind": "bool",
        },
        {
            "key": "auto_trigger_margin",
            "label": "Auto trigger margin (dB)",
            "val": lambda: (
                f"{cfg.auto_trigger_margin:.1f}"
                + ("" if cfg.auto_threshold else "  (unused: auto off)")
            ),
            "kind": "float",
            "range": AUTO_MARGIN_RANGE,
        },
        {
            "key": "manual_threshold",
            "label": "Manual threshold (dBFS)",
            "val": lambda: (
                f"{cfg.manual_threshold:.1f}"
                + ("" if not cfg.auto_threshold else "  (unused: auto on)")
            ),
            "kind": "float",
            "range": THRESHOLD_RANGE,
        },
        {
            "key": "pre_trigger_buffers",
            "label": "Pre-trigger buffers",
            "val": lambda: str(cfg.pre_trigger_buffers),
            "kind": "int",
            "range": PRE_TRIGGER_RANGE,
        },
        {
            "key": "silence_seconds",
            "label": "Silence seconds",
            "val": lambda: f"{cfg.silence_seconds:.2f}",
            "kind": "float",
            "range": SILENCE_RANGE,
        },
        {
            "key": "max_post_trigger_seconds",
            "label": "Max post-trigger seconds",
            "val": lambda: f"{cfg.max_post_trigger_seconds:.2f}",
            "kind": "float",
            "range": POST_TRIGGER_RANGE,
        },
        {
            "key": "rx_buffer_size",
            "label": "RX buffer size",
            "val": lambda: str(cfg.rx_buffer_size),
            "kind": "int",
            "range": RX_BUFFER_RANGE,
        },
        # --- GPS Geolocation ---
        {
            "key": "enable_gps",
            "label": "Enable GPS (gpsd)",
            "val": lambda: "yes" if cfg.enable_gps else "no",
            "kind": "bool",
        },
        {
            "key": "gpsd_host",
            "label": "gpsd host",
            "val": lambda: (
                cfg.gpsd_host + ("" if cfg.enable_gps else "  (unused: GPS off)")
            ),
            "kind": "text",
        },
        {
            "key": "gpsd_port",
            "label": "gpsd port",
            "val": lambda: (
                str(cfg.gpsd_port) + ("" if cfg.enable_gps else "  (unused: GPS off)")
            ),
            "kind": "int",
            "range": (1, 65535),
        },
        # --- Interface & System ---
        {
            "key": "enable_sound",
            "label": "Enable sound effects",
            "val": lambda: "yes" if cfg.enable_sound else "no",
            "kind": "bool",
        },
        {
            "key": "enable_mouse",
            "label": "Enable mouse support",
            "val": lambda: "yes" if cfg.enable_mouse else "no",
            "kind": "bool",
        },
    ]


def _guard_inactive_setting(
    key: str, cfg: AppConfig, win: curses.window | None
) -> bool:
    """Return True (and flash a warning) if the setting is currently inactive."""
    msg: str | None = None
    if key == "pluto_ip" and not cfg.pluto_uri.startswith("ip:"):
        msg = "USB mode is active. Switch 'Connection type' to 'ip' to set IP address."
    elif not cfg.enable_gps and key in ("gpsd_host", "gpsd_port"):
        msg = "GPS is disabled. Turn on 'Enable GPS (gpsd)' first."
    elif key == "auto_trigger_margin" and not cfg.auto_threshold:
        msg = "Auto margin is inactive (Auto threshold is off)."
    elif key == "manual_threshold" and cfg.auto_threshold:
        msg = "Manual threshold is inactive (Auto threshold is on)."

    if msg is not None:
        if win is not None:
            _flash(win, msg, C_WARN)
        return True
    return False


def _apply_setting(
    row: dict[str, Any], win: curses.window, cfg: AppConfig, redraw_fn: Any = None
) -> None:
    key, kind = row["key"], row["kind"]
    label = row["label"]

    if _guard_inactive_setting(key, cfg, win):
        return

    if key == "connection_type":
        if cfg.pluto_uri.startswith("ip:"):
            cfg.pluto_uri = "usb:"
        else:
            ip_val = cfg.pluto_ip.strip() if cfg.pluto_ip else "192.168.2.1"
            cfg.pluto_uri = f"ip:{ip_val}"
        return

    if key == "pluto_ip":
        prompt = f"{label}  (e.g. 192.168.2.1)"
        new = edit_text(win, prompt, cfg.pluto_ip, redraw_fn=redraw_fn)
        if new is not None:
            clean_ip = new.strip()
            if clean_ip.startswith("ip:"):
                clean_ip = clean_ip[3:].strip()
            if clean_ip:
                cfg.pluto_ip = clean_ip
                cfg.pluto_uri = f"ip:{clean_ip}"
        return

    cur_val = getattr(cfg, key)
    if kind == "text":
        prompt = f"{label}  (e.g. 127.0.0.1)" if key == "gpsd_host" else label
        new = edit_text(win, prompt, str(cur_val), redraw_fn=redraw_fn)
        if new is not None and new.strip():
            setattr(cfg, key, new.strip())
    elif kind == "bool":
        setattr(cfg, key, not cur_val)
    else:
        cast = int if kind == "int" else float
        lo, hi = row["range"]
        prompt = label
        if key in ("rx_freq", "tx_freq"):
            prompt = f"{label}  (e.g. 433.92M or 433920000)"
        elif key == "sample_rate":
            prompt = f"{label}  (e.g. 2M or 2000000)"
        new = edit_number(win, prompt, cur_val, cast, lo, hi, redraw_fn=redraw_fn)
        if new is not None:
            setattr(cfg, key, cast(new))


def _step_setting(
    row: dict[str, Any],
    cfg: AppConfig,
    direction: int,
    win: curses.window | None = None,
) -> bool:
    """Increment (+1) or decrement (-1) a setting directly in the list."""
    key = row["key"]
    kind = row["kind"]

    if _guard_inactive_setting(key, cfg, win):
        return False

    if key == "connection_type":
        cur_is_ip = cfg.pluto_uri.startswith("ip:")
        new_is_ip = direction >= 0
        if new_is_ip != cur_is_ip:
            if new_is_ip:
                ip_val = cfg.pluto_ip.strip() if cfg.pluto_ip else "192.168.2.1"
                cfg.pluto_uri = f"ip:{ip_val}"
            else:
                cfg.pluto_uri = "usb:"
            return True
        return False

    cur_val = getattr(cfg, key)

    if kind == "bool":
        new_val = direction >= 0
        if new_val != cur_val:
            setattr(cfg, key, new_val)
            return True
        return False

    if kind in ("int", "float"):
        lo, hi = row["range"]
        if key == "rx_buffer_size":
            new_buf = (
                min(hi, cur_val * 2 if cur_val < hi else hi)
                if direction > 0
                else max(lo, cur_val // 2 if cur_val > lo else lo)
            )
            if new_buf != cur_val:
                setattr(cfg, key, new_buf)
                return True
            return False

        steps: dict[str, float] = {
            "rx_gain": 1.0,
            "tx_gain": 1.0,
            "manual_threshold": 1.0,
            "auto_trigger_margin": 0.5,
            "silence_seconds": 0.05,
            "max_post_trigger_seconds": 0.5,
            "rx_freq": 10_000,
            "tx_freq": 10_000,
            "sample_rate": 250_000,
            "pre_trigger_buffers": 1,
        }
        step = steps.get(key, 1 if kind == "int" else 0.5)
        raw_val = cur_val + direction * step
        new_num: int | float = (
            max(int(lo), min(int(hi), round(raw_val)))
            if kind == "int"
            else max(float(lo), min(float(hi), round(raw_val, 2)))
        )
        if new_num != cur_val:
            setattr(cfg, key, new_num)
            return True
        return False

    return False


def screen_settings(win: curses.window, config: AppConfig | None = None) -> None:
    cfg = CONFIG if config is None else config
    idx = 0
    top = 0
    win.nodelay(False)
    backup = dict(cfg.to_dict())
    row_hitboxes: dict[int, Any] = {}

    def render_settings_list(current_rows: list[dict[str, Any]] | None = None) -> int:
        nonlocal top
        rows = _build_setting_rows(cfg) if current_rows is None else current_rows
        start = draw_chrome(win, "Settings", SETTINGS_HINT)
        row_hitboxes.clear()
        if start == -1:
            return -1
        avail, _ = _content_rows(win, start, 4)
        val_col = 38
        if idx < top:
            top = idx
        elif idx >= top + avail:
            top = idx - avail + 1
        top = max(0, min(top, max(0, len(rows) - avail)))
        for i in range(top, min(len(rows), top + avail)):
            row = start + (i - top)
            setting = rows[i]
            value = setting["val"]()
            value_text = f" {value} " if i == idx else value
            label_width = max(0, val_col - 4 - 3)
            sep = " " if not value_text.startswith(" ") and len(setting["label"]) >= label_width else ""
            line = setting["label"].ljust(label_width) + sep + value_text
            row_hitboxes[i] = draw_list_row(win, row, line, i == idx)
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
            init_mouse(cfg.enable_mouse)
            init_gps(cfg)
            _flash(win, "Defaults restored.", C_OK)
            return True
        return False

    def post_change_sync(target_row: dict[str, Any]) -> None:
        flush_input(win)
        before = dict(cfg.to_dict())
        validated = validate_config(before).to_dict()
        cfg.update(validated)
        reset = [key for key, val in validated.items() if before[key] != val]
        if reset:
            _flash(win, "Out of range, reset to default: " + ", ".join(reset), C_WARN)
        init_mouse(cfg.enable_mouse)
        if target_row["key"] in ("enable_gps", "gpsd_host", "gpsd_port"):
            init_gps(cfg)
        if target_row["key"] == "enable_sound" and cfg.enable_sound:
            play_cue(SoundCue.PASS, enabled=True)

    def handle_exit() -> bool:
        if cfg.to_dict() != backup:
            if confirm_dialog(win, "Unsaved Changes", "Discard unsaved changes?"):
                cfg.update(backup)
                init_mouse(cfg.enable_mouse)
                init_gps(cfg)
                return True
            return False
        return True

    while True:
        rows = _build_setting_rows(cfg)
        start = render_settings_list(rows)
        if start == -1:
            win.refresh()
            k = win.getch()
            if (k in (KEY_ESC, ord("q"), ord("Q"))) and handle_exit():
                return
            continue

        avail, _ = _content_rows(win, start, 4)
        win.refresh()
        k = win.getch()
        if handle_common_key(k):
            continue

        if k in KEY_ENTER or (k == ord(" ") and rows[idx]["kind"] == "bool"):
            _apply_setting(rows[idx], win, cfg, redraw_fn=render_settings_list)
            post_change_sync(rows[idx])
        elif k in (getattr(curses, "KEY_LEFT", 260), ord("h")):
            if _step_setting(rows[idx], cfg, -1, win=win):
                post_change_sync(rows[idx])
        elif k in (getattr(curses, "KEY_RIGHT", 261), ord("l")):
            if _step_setting(rows[idx], cfg, +1, win=win):
                post_change_sync(rows[idx])
        elif k in (KEY_ESC, ord("q"), ord("Q")):
            if handle_exit():
                return
        elif k == getattr(curses, "KEY_MOUSE", -1):
            m = get_mouse_event()
            if m is not None:
                _mid, mx, my, _mz, bstate = m
                if bstate & WHEEL_UP:
                    idx = max(0, idx - 1)
                elif bstate & WHEEL_DOWN:
                    idx = min(len(rows) - 1, idx + 1)
                elif bstate & BUTTON_RIGHT:
                    if handle_exit():
                        return
                elif bstate & (BUTTON_CLICK | BUTTON_DOUBLE):
                    clicked_idx = top + (my - start)
                    hitbox = row_hitboxes.get(clicked_idx)
                    if hitbox is not None and hitbox.contains(mx, my):
                        if clicked_idx == idx or (bstate & BUTTON_DOUBLE):
                            idx = clicked_idx
                            _apply_setting(
                                rows[clicked_idx],
                                win,
                                cfg,
                                redraw_fn=render_settings_list,
                            )
                            post_change_sync(rows[clicked_idx])
                        else:
                            idx = clicked_idx
        else:
            idx = _move_cursor(idx, k, len(rows), avail)
