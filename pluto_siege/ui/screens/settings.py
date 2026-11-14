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
import os
from typing import Any

from pluto_siege.config import (
    CONFIG,
    DEFAULT_SETTINGS,
    AppConfig,
    normalize_pluto_uri,
    parse_rf_bandwidth,
    save_settings,
    validate_config,
)
from pluto_siege.constants import (
    AUTO_MARGIN_RANGE,
    AUTO_RECAL_RANGE,
    FREQ_RANGE,
    MIN_BURST_RANGE,
    POST_TRIGGER_RANGE,
    PRE_TRIGGER_RANGE,
    RF_BANDWIDTH_RANGE,
    RX_BUFFER_RANGE,
    RX_GAIN_RANGE,
    SAMPLE_RATE_RANGE,
    SILENCE_RANGE,
    THRESHOLD_RANGE,
    TX_GAIN_RANGE,
    TX_REPEATS_RANGE,
)
from pluto_siege.device import resolve_rf_bandwidth
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
    MENU_X,
    WHEEL_DOWN,
    WHEEL_UP,
    _content_rows,
    _flash,
    _move_cursor,
    _reset_flash_timeout,
    _set_flash_timeout,
    clear_flash,
    confirm_dialog,
    draw_chrome,
    draw_flash,
    draw_list_row,
    edit_number,
    edit_text,
    get_mouse_event,
    init_mouse,
)

SETTINGS_HINT = (
    "Up/Down = move ▎ Left/Right = adjust ▎ Enter = edit ▎ S = save ▎ R = reset ▎ Esc/Q = back"
)


def _build_setting_rows(cfg: AppConfig) -> list[dict[str, Any]]:
    return [
        # --- Radio & Connection ---
        {
            "key": "pluto_uri",
            "label": "Pluto URI",
            "val": lambda: cfg.pluto_uri,
            "kind": "text",
        },
        {
            "key": "rx_freq",
            "label": "RX freq (Hz)",
            "val": lambda: f"{cfg.rx_freq}  ({cfg.rx_freq / 1e6:.3f} MHz)",
            "kind": "int",
            "range": FREQ_RANGE,
        },
        {
            "key": "sample_rate",
            "label": "Sample rate (Hz)",
            "val": lambda: f"{cfg.sample_rate}  ({cfg.sample_rate / 1e6:.2f} MSPS)",
            "kind": "int",
            "range": SAMPLE_RATE_RANGE,
        },
        {
            "key": "rf_bandwidth",
            "label": "RF bandwidth (Hz)",
            "val": lambda: (
                f"0  (auto: {resolve_rf_bandwidth(cfg.sample_rate, 0) / 1e6:.2f} MHz)"
                if cfg.rf_bandwidth == 0
                else f"{cfg.rf_bandwidth}  ({cfg.rf_bandwidth / 1e6:.2f} MHz)"
            ),
            "kind": "int",
            "range": RF_BANDWIDTH_RANGE,
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
        {
            "key": "tx_repeats",
            "label": "TX repeats",
            "val": lambda: str(cfg.tx_repeats),
            "kind": "int",
            "range": TX_REPEATS_RANGE,
        },
        # --- Capture Engine & Trigger ---
        {
            "key": "auto_capture",
            "label": "Auto capture",
            "val": lambda: "yes" if cfg.auto_capture else "no",
            "kind": "bool",
        },
        {
            "key": "auto_threshold",
            "label": "Auto threshold",
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
            "key": "auto_recal_seconds",
            "label": "Auto recalibrate seconds",
            "val": lambda: (
                f"{cfg.auto_recal_seconds:.1f}  (unused: auto off)"
                if not cfg.auto_threshold
                else (
                    "0.0  (off)"
                    if cfg.auto_recal_seconds == 0
                    else f"{cfg.auto_recal_seconds:.1f}"
                )
            ),
            "kind": "float",
            "range": AUTO_RECAL_RANGE,
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
            "key": "min_burst_seconds",
            "label": "Min burst seconds",
            "val": lambda: (
                "0.000  (off)"
                if cfg.min_burst_seconds == 0
                else f"{cfg.min_burst_seconds:.3f}  ({cfg.min_burst_seconds * 1000:.0f} ms)"
            ),
            "kind": "float",
            "range": MIN_BURST_RANGE,
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
            "val": lambda: cfg.gpsd_host + ("" if cfg.enable_gps else "  (unused: GPS off)"),
            "kind": "text",
        },
        {
            "key": "gpsd_port",
            "label": "gpsd port",
            "val": lambda: str(cfg.gpsd_port) + ("" if cfg.enable_gps else "  (unused: GPS off)"),
            "kind": "int",
            "range": (1, 65535),
        },
        # --- Interface & System ---
        {
            "key": "records_dir",
            "label": "Records dir",
            "val": lambda: cfg.records_dir,
            "kind": "text",
        },
        {
            "key": "enable_sound",
            "label": "Enable sound",
            "val": lambda: "yes" if cfg.enable_sound else "no",
            "kind": "bool",
        },
        {
            "key": "enable_mouse",
            "label": "Enable mouse",
            "val": lambda: "yes" if cfg.enable_mouse else "no",
            "kind": "bool",
        },
    ]


def _guard_inactive_setting(key: str, cfg: AppConfig, win: curses.window | None) -> bool:
    """Return True (and flash a warning) if the setting is currently inactive."""
    msg: str | None = None
    if not cfg.enable_gps and key in ("gpsd_host", "gpsd_port"):
        msg = "GPS is disabled. Turn on 'Enable GPS (gpsd)' first."
    elif key == "auto_trigger_margin" and not cfg.auto_threshold:
        msg = "Auto margin is inactive (Auto threshold is off)."
    elif key == "auto_recal_seconds" and not cfg.auto_threshold:
        msg = "Auto recalibrate is inactive (Auto threshold is off)."
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

    if key == "pluto_uri":
        prompt = f"{label}  (e.g. usb:, ip:192.168.2.1, usb:1.4.5)"
        text = cfg.pluto_uri
        while True:
            raw = edit_text(win, prompt, text, redraw_fn=redraw_fn)
            if raw is None:
                return
            raw = raw.strip()
            if raw == "":
                return
            text = raw
            try:
                cfg.pluto_uri = normalize_pluto_uri(raw)
                return
            except ValueError as err:
                _flash(win, str(err) if str(err) else "Invalid Pluto URI.", C_ERR)

    if key == "rf_bandwidth":
        prompt = f"{label}  (0/auto or 200k .. 56M)"
        text = str(cfg.rf_bandwidth)
        while True:
            raw = edit_text(win, prompt, text, redraw_fn=redraw_fn)
            if raw is None:
                return
            raw = raw.strip()
            if raw == "":
                return
            text = raw
            try:
                cfg.rf_bandwidth = parse_rf_bandwidth(raw)
                return
            except ValueError as err:
                _flash(win, str(err) if str(err) else "Invalid bandwidth.", C_ERR)
            except OverflowError:
                _flash(win, "Invalid bandwidth.", C_ERR)

    cur_val = getattr(cfg, key)
    if kind == "text":
        prompt = f"{label}  (e.g. 127.0.0.1)" if key == "gpsd_host" else label
        new = edit_text(win, prompt, str(cur_val), redraw_fn=redraw_fn)
        if new is not None and new.strip():
            cleaned = new.strip()
            if key == "records_dir":
                if "\x00" in cleaned or not all(ch.isprintable() for ch in cleaned):
                    _flash(win, "Invalid directory path.", C_ERR)
                    return
                setattr(cfg, key, os.path.expanduser(cleaned))
            else:
                setattr(cfg, key, cleaned)
    elif kind == "bool":
        setattr(cfg, key, not cur_val)
    else:
        cast = int if kind == "int" else float
        lo, hi = row["range"]
        prompt = label
        if key == "rx_freq":
            prompt = f"{label}  (e.g. 433.92M or 433920000)"
        elif key == "sample_rate":
            prompt = f"{label}  (e.g. 2M or 2000000)"
        elif key == "auto_recal_seconds":
            prompt = f"{label}  (0 = off, e.g. 15 or 30s)"
        elif key == "min_burst_seconds":
            prompt = f"{label}  (e.g. 0, 10ms, or 0.02)"
        elif key == "silence_seconds":
            prompt = f"{label}  (e.g. 0.8 or 200ms)"
        elif key == "max_post_trigger_seconds":
            prompt = f"{label}  (e.g. 4.0 or 500ms)"
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

    if key == "pluto_uri":
        if direction > 0 and not cfg.pluto_uri.startswith("ip:"):
            cfg.pluto_uri = "ip:192.168.2.1"
            return True
        if direction < 0 and cfg.pluto_uri != "usb:":
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
        if key == "rf_bandwidth":
            if cur_val == 0:
                if direction > 0:
                    setattr(cfg, key, RF_BANDWIDTH_RANGE[0])
                    return True
                return False
            if direction < 0:
                new_bw = (
                    0
                    if cur_val <= RF_BANDWIDTH_RANGE[0]
                    else max(RF_BANDWIDTH_RANGE[0], cur_val - 250_000)
                )
            else:
                new_bw = (
                    250_000
                    if cur_val == RF_BANDWIDTH_RANGE[0]
                    else min(RF_BANDWIDTH_RANGE[1], cur_val + 250_000)
                )
            if new_bw != cur_val:
                setattr(cfg, key, new_bw)
                return True
            return False

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
            "tx_repeats": 1,
            "manual_threshold": 1.0,
            "auto_trigger_margin": 0.5,
            "auto_recal_seconds": 5.0,
            "min_burst_seconds": 0.005,
            "silence_seconds": 0.05,
            "max_post_trigger_seconds": 0.5,
            "rx_freq": 10_000,
            "sample_rate": 250_000,
            "pre_trigger_buffers": 1,
        }
        step = steps.get(key, 1 if kind == "int" else 0.5)
        raw_val = cur_val + direction * step
        if kind == "int":
            clamped = round(raw_val)
            if lo is not None:
                clamped = max(int(lo), clamped)
            if hi is not None:
                clamped = min(int(hi), clamped)
            new_num: int | float = clamped
        else:
            clamped_f = round(raw_val, 4)
            if lo is not None:
                clamped_f = max(float(lo), clamped_f)
            if hi is not None:
                clamped_f = min(float(hi), clamped_f)
            new_num = clamped_f
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
            label_width = max(0, val_col - MENU_X - 3)
            sep = " " if len(setting["label"]) >= label_width else ""
            line = setting["label"].ljust(label_width) + sep + value
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
        before = dict(cfg.to_dict())
        validated = validate_config(before).to_dict()
        cfg.update(validated)
        reset = [key for key, val in validated.items() if before[key] != val]
        if reset:
            _flash(win, "Out of range, reset to default: " + ", ".join(reset), C_WARN)
        if cfg.min_burst_seconds > cfg.max_post_trigger_seconds:
            cfg.min_burst_seconds = cfg.max_post_trigger_seconds
            _flash(
                win,
                "Clamped min_burst_seconds to max_post_trigger_seconds.",
                C_WARN,
            )
        init_mouse(cfg.enable_mouse)
        if target_row["key"] in ("enable_gps", "gpsd_host", "gpsd_port"):
            init_gps(cfg)
        if target_row["key"] == "enable_sound" and cfg.enable_sound:
            play_cue(SoundCue.PASS, enabled=True)

    def handle_exit() -> bool:
        clear_flash()
        _reset_flash_timeout(win)
        if cfg.to_dict() != backup:
            if confirm_dialog(win, "Unsaved Changes", "Discard unsaved changes?"):
                cfg.update(backup)
                init_mouse(cfg.enable_mouse)
                init_gps(cfg)
                return True
            return False
        return True

    try:
        while True:
            rows = _build_setting_rows(cfg)
            start = render_settings_list(rows)
            if start == -1:
                win.refresh()
                _reset_flash_timeout(win)
                k = win.getch()
                if (k in (KEY_ESC, ord("q"), ord("Q"))) and handle_exit():
                    return
                continue

            avail, _ = _content_rows(win, start, 4)
            draw_flash(win)
            win.refresh()
            _set_flash_timeout(win)
            k = win.getch()
            if k == -1:
                continue
            if k != getattr(curses, "KEY_RESIZE", -1):
                clear_flash()
            if handle_common_key(k):
                continue

            if k in KEY_ENTER or (k == ord(" ") and rows[idx]["kind"] == "bool"):
                _apply_setting(rows[idx], win, cfg, redraw_fn=render_settings_list)
                post_change_sync(rows[idx])
            elif k in (getattr(curses, "KEY_LEFT", 260), ord("h"), ord("H")):
                if _step_setting(rows[idx], cfg, -1, win=win):
                    post_change_sync(rows[idx])
            elif k in (getattr(curses, "KEY_RIGHT", 261), ord("l"), ord("L")):
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
    finally:
        clear_flash()
        _reset_flash_timeout(win)
