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

"""Transmit screen: pick a recording and replay it."""

import curses
import datetime
import glob
import os
from typing import Optional

from pluto_siege.config import AppConfig
from pluto_siege.constants import (
    BYTES_PER_SAMPLE,
    IO_TIMEOUT_UNAVAILABLE,
    RECORDS_DIR,
)
from pluto_siege.device import SDRDevice
from pluto_siege.engine import TransmitEngine
from pluto_siege.sigmf import load_sigmf_meta
from pluto_siege.ui.widgets.framework import (
    C_DIM,
    C_ERR,
    C_OK,
    C_WARN,
    KEY_ENTER,
    KEY_ESC,
    _tail,
    confirm_dialog,
    draw_chrome,
    flush_input,
    menu_select,
    message_box,
    scroll_view,
)

DELETE_KEYS = (curses.KEY_DC, curses.KEY_BACKSPACE, 8, 127, ord('d'), ord('D'))


def _label_for_recording(f: str) -> str:
    """Human-readable label from a recording filename with freq, sample rate, and sample count."""
    name = os.path.basename(f).removeprefix("rec_").removesuffix(".sigmf-data")
    parts = name.split("_")
    try:
        use_sr, use_freq, _ = load_sigmf_meta(f)
        n_samples = os.path.getsize(f) // BYTES_PER_SAMPLE
        if len(parts) >= 3:
            dt = datetime.datetime.strptime("_".join(parts[:2]), "%Y%m%d_%H%M%S")
            dt_str = f"{dt:%Y-%m-%d %H:%M:%S}"
        else:
            dt_str = name
        return f"{dt_str}  {use_freq / 1e6:.3f} MHz  {use_sr / 1e6:.2f} MSPS  {n_samples:,} Samples"
    except Exception:
        return name


def pick_recording(win: "curses.window") -> Optional[str]:
    try:
        os.makedirs(RECORDS_DIR, exist_ok=True)
    except OSError as e:
        message_box(win, "Transmit",
                    [(f"Cannot open recordings folder: {e}", C_ERR)])
        return None

    sel_idx = 0
    while True:
        files = sorted(glob.glob(os.path.join(RECORDS_DIR, "*.sigmf-data")), reverse=True)
        if not files:
            message_box(win, "Select Recording to Transmit (0 found)", [("No recordings found. Capture a key first.", C_WARN)])
            return None

        labels = [_label_for_recording(f) for f in files]
        sel, key = menu_select(
            win,
            f"Select Recording to Transmit ({len(files)} found)",
            labels,
            start_idx=sel_idx,
            hint="Up/Down = move ▎ Enter = play ▎ Del/Backspace/D = delete ▎ Esc = back",
            action_keys=DELETE_KEYS,
        )

        if sel is None or key == KEY_ESC:
            return None

        sel_idx = sel
        target_file = files[sel]

        if key in DELETE_KEYS:
            filename = os.path.basename(target_file)
            if confirm_dialog(
                win,
                "Delete Recording",
                f"Delete recording {filename}?",
            ):
                try:
                    os.remove(target_file)
                    meta_file = target_file.removesuffix(".sigmf-data") + ".sigmf-meta"
                    if os.path.exists(meta_file):
                        os.remove(meta_file)
                except OSError as err:
                    message_box(win, "Delete Recording", [(f"Failed to delete: {err}", C_ERR)])
            continue

        return target_file


def do_transmit(
    win: "curses.window",
    sdr: SDRDevice,
    path: str,
    config: Optional[AppConfig] = None,
) -> int:
    """Replay one recording, then show the result. Returns the key that closed it."""
    log: list[tuple[str, int]] = []

    def render(extra_hint: str = "Please wait...") -> None:
        start = draw_chrome(win, "Transmit Mode", extra_hint)
        _tail(log, win, start, 2)
        win.refresh()

    try:
        log.append(("Configuring transmitter...", C_DIM))
        render()

        bounds = config.freq_bounds if config is not None else None
        data, use_sr, use_freq = TransmitEngine.prepare_transmission(path, bounds=bounds)

        log.append((f"{use_freq / 1e6:.3f} MHz  {use_sr / 1e6:.2f} MSPS  {data.size:,} Samples", C_DIM))
        log.append((f"Transmitting recording: {os.path.basename(path)}...", C_DIM))
        render()

        timeout_ok = TransmitEngine.transmit(sdr, data, use_sr, use_freq, config=config)
        if not timeout_ok:
            log.append((IO_TIMEOUT_UNAVAILABLE, C_WARN))

        log.append(("Signal replayed successfully!", C_OK))
    except Exception as e:
        log.append((f"TX failed: {e}", C_ERR))

    flush_input(win)
    return scroll_view(win, "Transmit Complete", log,
                       hint="Enter = replay ▎ Esc = back", exit_keys=(KEY_ESC,) + KEY_ENTER)


def screen_transmit(
    win: "curses.window", sdr: SDRDevice, config: Optional[AppConfig] = None
) -> None:
    path = pick_recording(win)
    if path is None:
        return
    while True:
        if do_transmit(win, sdr, path, config=config) == KEY_ESC:
            return
