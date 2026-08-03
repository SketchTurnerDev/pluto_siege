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
import math
import os
import stat
import time
from typing import Optional

import numpy as np

from pluto_siege.constants import (
    BYTES_PER_SAMPLE,
    IO_TIMEOUT_UNAVAILABLE,
    MAX_TX_BURST_SAMPLES,
    RECORDS_DIR,
    TX_BACKOFF,
    TX_DAC_MAX,
    TX_DRAIN_MARGIN_SECONDS,
)
from pluto_siege.device import SDRDevice, cfg_tx, cleanup_sdr, set_io_timeout, suppress_c_stderr
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


def load_tx_payload(path: str) -> np.ndarray:
    """Read a recording and scale it for the DAC. Raises ValueError if unusable."""
    st = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("Target is not a regular file.")
    if st.st_size == 0:
        raise ValueError("Recording is empty")
    if st.st_size % BYTES_PER_SAMPLE != 0:
        raise ValueError("File size not aligned to complex64")
    n_samples = st.st_size // BYTES_PER_SAMPLE
    if n_samples > MAX_TX_BURST_SAMPLES:
        raise ValueError(f"Recording too large: {n_samples} samples "
                         f"(limit {MAX_TX_BURST_SAMPLES})")

    data = np.fromfile(path, dtype="<c8", count=n_samples)
    if data.size != n_samples:
        raise ValueError("Recording shrank while being read")
    data = np.asarray(data, dtype=np.complex64)

    flat = data.view(np.float32)
    hi, lo = float(flat.max()), float(flat.min())
    if not (math.isfinite(hi) and math.isfinite(lo)):
        raise ValueError("NaN/Inf in recording")
    peak = max(hi, -lo)
    if peak < 1e-6:
        raise ValueError("Recording is too quiet or empty")
    data *= np.float32(TX_BACKOFF * TX_DAC_MAX / peak)
    return data


def do_transmit(win: "curses.window", sdr: SDRDevice, path: str) -> int:
    """Replay one recording, then show the result. Returns the key that closed it."""
    log: list[tuple[str, int]] = []

    def render(extra_hint: str = "Please wait...") -> None:
        start = draw_chrome(win, "Transmit Mode", extra_hint)
        _tail(log, win, start, 2)
        win.refresh()

    try:
        log.append(("Configuring transmitter...", C_DIM))
        render()

        use_sr, use_freq, problem = load_sigmf_meta(path)
        if problem is not None:
            raise ValueError(f"untrusted recording - {problem}")

        data = load_tx_payload(path)

        cfg_tx(sdr, use_sr, use_freq)
        actual_sr = int(sdr.sample_rate)

        if not set_io_timeout(sdr, data.size / actual_sr):
            log.append((IO_TIMEOUT_UNAVAILABLE, C_WARN))

        log.append((f"{use_freq / 1e6:.3f} MHz  {use_sr / 1e6:.2f} MSPS  {data.size:,} Samples", C_DIM))
        log.append((f"Transmitting recording: {os.path.basename(path)}...", C_DIM))
        render()
        try:
            on_air_end = time.monotonic() + data.size / actual_sr
            with suppress_c_stderr():
                sdr.tx(data)
            drain = on_air_end + TX_DRAIN_MARGIN_SECONDS - time.monotonic()
            if drain > 0:
                time.sleep(drain)
        finally:
            cleanup_sdr(sdr)

        log.append(("Signal replayed successfully!", C_OK))
    except Exception as e:
        log.append((f"TX failed: {e}", C_ERR))
        cleanup_sdr(sdr)

    flush_input(win)
    return scroll_view(win, "Transmit Complete", log,
                       hint="Enter = replay ▎ Esc = back", exit_keys=(KEY_ESC,) + KEY_ENTER)


def screen_transmit(win: "curses.window", sdr: SDRDevice) -> None:
    path = pick_recording(win)
    if path is None:
        return
    while True:
        if do_transmit(win, sdr, path) == KEY_ESC:
            return
