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

from __future__ import annotations

import curses
import datetime
import glob
import os

from pluto_siege.config import CONFIG, AppConfig
from pluto_siege.constants import (
    BYTES_PER_SAMPLE,
    IO_TIMEOUT_UNAVAILABLE,
    RECORDS_DIR,
)
from pluto_siege.device import SDRDevice
from pluto_siege.engine import TransmitEngine
from pluto_siege.sigmf import load_sigmf_geolocation, load_sigmf_meta
from pluto_siege.sound import SoundCue, play_cue
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
    screen_is_usable,
    scroll_view,
    wait_for_usable_screen,
)

DELETE_KEYS = (curses.KEY_DC, curses.KEY_BACKSPACE, 8, 127, ord("d"), ord("D"))


def _label_for_recording(f: str, bounds: tuple[int, int] | None = None) -> str:
    """Human-readable label from a recording filename with freq, sample rate, sample count, and GPS tag."""
    name = os.path.basename(f).removeprefix("rec_").removesuffix(".sigmf-data")
    parts = name.split("_")
    try:
        use_sr, use_freq, err = load_sigmf_meta(f, bounds=bounds)
        if err is not None or use_sr is None or use_freq is None:
            return name
        n_samples = os.path.getsize(f) // BYTES_PER_SAMPLE
        if len(parts) >= 3:
            dt = datetime.datetime.strptime(
                "_".join(parts[:2]), "%Y%m%d_%H%M%S"
            ).replace(tzinfo=datetime.timezone.utc)
            dt_str = f"{dt:%Y-%m-%d %H:%M:%S}"
        else:
            dt_str = name
        geo = load_sigmf_geolocation(f)
        if geo is not None:
            lon, lat, _alt = geo
            lat_card = "N" if lat >= 0 else "S"
            lon_card = "E" if lon >= 0 else "W"
            geo_tag = f"  [{abs(lat):.3f}°{lat_card}, {abs(lon):.3f}°{lon_card}]"
        else:
            geo_tag = ""
        return (
            f"{dt_str}  {use_freq / 1e6:.3f} MHz  "
            f"{use_sr / 1e6:.2f} MSPS  {n_samples:,} Samples{geo_tag}"
        )
    except (OSError, ValueError, KeyError):
        return name


def pick_recording(
    win: curses.window, bounds: tuple[int, int] | None = None
) -> str | None:
    try:
        os.makedirs(RECORDS_DIR, exist_ok=True)
    except OSError as e:
        message_box(win, "Transmit", [(f"Cannot open recordings folder: {e}", C_ERR)])
        return None

    sel_idx = 0
    while True:
        files = sorted(
            glob.glob(os.path.join(RECORDS_DIR, "*.sigmf-data")), reverse=True
        )
        if not files:
            message_box(
                win,
                "Select Recording to Transmit (0 found)",
                [("No recordings found. Capture a key first.", C_WARN)],
            )
            return None

        labels = [_label_for_recording(f, bounds=bounds) for f in files]
        sel, key = menu_select(
            win,
            f"Select Recording to Transmit ({len(files)} found)",
            labels,
            start_idx=sel_idx,
            hint="Up/Down = move ▎ Enter = transmit ▎ D/Del = delete ▎ Esc/Q = back",
            action_keys=DELETE_KEYS,
        )

        if sel is None or key in (KEY_ESC, ord("q"), ord("Q")):
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
                meta_file = target_file.removesuffix(".sigmf-data") + ".sigmf-meta"
                try:
                    os.remove(target_file)
                except OSError as err:
                    message_box(
                        win, "Delete Recording", [(f"Failed to delete: {err}", C_ERR)]
                    )
                    continue
                try:
                    if os.path.exists(meta_file):
                        os.remove(meta_file)
                except OSError as err:
                    message_box(
                        win,
                        "Delete Recording",
                        [(f"Failed to delete metadata: {err}", C_ERR)],
                    )
            continue

        return target_file


def do_transmit(
    win: curses.window,
    sdr: SDRDevice,
    path: str,
    config: AppConfig | None = None,
) -> int:
    """Replay one recording, then show the result. Returns the key that closed it."""
    cfg = CONFIG if config is None else config
    if not screen_is_usable(win) and not wait_for_usable_screen(win):
        return KEY_ESC
    log: list[tuple[str, int]] = []
    tx_ok = True

    def render(extra_hint: str = "Please wait...") -> None:
        start = draw_chrome(win, "Transmit Mode", extra_hint)
        if start != -1:
            _tail(log, win, start, 2)
            win.refresh()

    try:
        log.append(("Configuring transmitter...", C_DIM))
        render()

        data, use_sr, use_freq = TransmitEngine.prepare_transmission(
            path, bounds=cfg.freq_bounds
        )

        sound_enabled = cfg.enable_sound
        log.append(
            (
                f"{use_freq / 1e6:.3f} MHz  {use_sr / 1e6:.2f} MSPS  {data.size:,} Samples",
                C_DIM,
            )
        )
        geo = load_sigmf_geolocation(path)
        if geo is not None:
            lon, lat, alt = geo
            lat_card = "N" if lat >= 0 else "S"
            lon_card = "E" if lon >= 0 else "W"
            log.append(
                (
                    f"Geotag: {abs(lat):.5f}°{lat_card}, {abs(lon):.5f}°{lon_card} ({alt:.1f} m)",
                    C_DIM,
                )
            )
        log.append((f"Transmitting recording: {os.path.basename(path)}...", C_DIM))
        play_cue(SoundCue.TX_START, enabled=sound_enabled)
        render()

        timeout_ok = TransmitEngine.transmit(sdr, data, use_sr, use_freq, config=cfg)
        if not timeout_ok:
            log.append((IO_TIMEOUT_UNAVAILABLE, C_WARN))

        play_cue(SoundCue.TX_COMPLETE, enabled=sound_enabled)
        log.append(("Signal replayed successfully!", C_OK))
    except (OSError, RuntimeError, ValueError) as e:
        tx_ok = False
        play_cue(SoundCue.FAIL, enabled=cfg.enable_sound)
        log.append((f"TX failed: {e}", C_ERR))

    flush_input(win)
    title = "Transmit Complete" if tx_ok else "Transmit Failed"
    hint = "Enter/R = replay ▎ Esc/Q = back" if tx_ok else "Enter/R = retry ▎ Esc/Q = back"
    return scroll_view(
        win,
        title,
        log,
        hint=hint,
        exit_keys=(KEY_ESC, ord("q"), ord("Q"), ord("r"), ord("R")) + KEY_ENTER,
    )


def screen_transmit(
    win: curses.window, sdr: SDRDevice, config: AppConfig | None = None
) -> None:
    cfg = CONFIG if config is None else config
    bounds = cfg.freq_bounds
    while True:
        path = pick_recording(win, bounds=bounds)
        if path is None:
            return
        while True:
            key = do_transmit(win, sdr, path, config=cfg)
            if key not in KEY_ENTER and key not in (ord("r"), ord("R")):
                break
