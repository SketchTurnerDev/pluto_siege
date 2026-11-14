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

from __future__ import annotations

import contextlib
import io
import json
import os
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from pluto_siege import cli, config
from pluto_siege.config import AppConfig
from pluto_siege.constants import TX_CHUNK_SAMPLES, brief
from pluto_siege.device import cfg_rx, cfg_tx, cleanup_sdr, release_sdr, set_io_timeout
from pluto_siege.dsp import max_subwindow_dbfs, peak_window_dbfs
from pluto_siege.engine import CaptureEngine, LoopbackTester, TransmitEngine
from pluto_siege.gps import GPSFix
from pluto_siege.settings import validate_settings
from pluto_siege.sigmf import load_sigmf_meta, save_sigmf_pair
from pluto_siege.sound import _CUES, SoundCue, _generate_wav
from pluto_siege.ui.screens import main_menu
from pluto_siege.ui.screens.capture import screen_capture
from pluto_siege.ui.screens.settings import screen_settings
from pluto_siege.ui.screens.transmit import (
    _label_for_recording,
    do_transmit,
    screen_transmit,
)
from pluto_siege.ui.widgets.framework import (
    KEY_ESC,
    confirm_dialog,
    draw_chrome,
    draw_gps_status,
    hide_cursor,
    menu_select,
    parse_number,
    scroll_view,
    set_cursor_visible,
)


class FakeContext:
    def __init__(self) -> None:
        self.timeout_ms = 0

    def set_timeout(self, ms: int) -> None:
        self.timeout_ms = ms


class FakeSDR:
    """In-memory SDR stub supporting RX, TX, and digital loopback."""

    def __init__(self, rx_queue: list[NDArray[np.complex64]] | None = None) -> None:
        self.events: list[str] = []
        self.sample_rate = 2_100_000
        self.rx_lo = 433_920_000
        self.tx_lo = 433_920_000
        self.rx_buffer_size = 1024
        self.rx_rf_bandwidth = 2_100_000
        self.tx_rf_bandwidth = 2_100_000
        self.rx_hardwaregain_chan0 = 15.0
        self.tx_hardwaregain_chan0 = 0.0
        self.gain_control_mode_chan0 = "manual"
        self.tx_cyclic_buffer = False
        self.loopback = 0
        self.ctx: Any = FakeContext()
        self._rx_queue = list(rx_queue or [])
        self.last_tx: NDArray[np.complex64] | None = None
        self.tx_count = 0

    def __setattr__(self, name: str, value: Any) -> None:
        if "events" in self.__dict__ and name in (
            "loopback",
            "tx_cyclic_buffer",
            "tx_hardwaregain_chan0",
        ):
            self.events.append(f"set:{name}={value}")
        super().__setattr__(name, value)

    def rx(self) -> NDArray[np.complex64]:
        if self.loopback == 1 and self.last_tx is not None:
            return self.last_tx.copy()
        if self._rx_queue:
            return self._rx_queue.pop(0)
        return np.ones(self.rx_buffer_size, dtype=np.complex64) * (2.0 + 2.0j)

    def tx(self, data: NDArray[np.complex64]) -> None:
        # Simulate 1 Hz fractional-N PLL rounding on real AD936x silicon
        self.tx_lo = self.tx_lo - 1
        self.last_tx = data.copy()
        self.tx_count += 1

    def rx_destroy_buffer(self) -> None:
        self.events.append("rx_destroy_buffer")

    def tx_destroy_buffer(self) -> None:
        self.events.append("tx_destroy_buffer")


class FakeWindow:
    """Minimal curses.window double for headless UI framework testing."""

    def __init__(self, height: int = 40, width: int = 110) -> None:
        self.height = height
        self.width = width
        self.calls: list[tuple[int, int, str, int]] = []
        self.keys: list[int] = []
        self.nodelay_keys: list[int] = []
        self._nodelay = False

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def getch(self) -> int:
        if self._nodelay:
            return self.nodelay_keys.pop(0) if self.nodelay_keys else -1
        return self.keys.pop(0) if self.keys else KEY_ESC

    def nodelay(self, flag: bool) -> None:
        self._nodelay = flag

    def move(self, y: int, x: int) -> None:
        pass

    def erase(self) -> None:
        self.calls.clear()

    def border(self, *args: object) -> None:
        pass

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        self.calls.append((y, x, text, attr))

    def keypad(self, enabled: bool) -> None:
        pass

    def refresh(self) -> None:
        pass


def test_capture_and_transmit_round_trip_with_pll_rounding(tmp_path) -> None:
    quiet = np.ones(1024, dtype=np.complex64) * (1.0 + 1.0j)
    burst = np.ones(1024, dtype=np.complex64) * (800.0 + 600.0j)
    rx_buffers = [quiet] * 16 + [burst, burst] + [quiet] * 8

    sdr = FakeSDR(rx_queue=rx_buffers)
    cfg = AppConfig(
        rx_buffer_size=1024,
        pre_trigger_buffers=2,
        silence_seconds=0.1,
        max_post_trigger_seconds=0.5,
    )
    engine = CaptureEngine(sdr, config=cfg)
    engine.prepare()
    engine.listen_and_capture(check_abort=lambda: False)

    assert engine.captured_data is not None
    assert engine.captured_data.size > 0
    assert not engine.is_aborted

    base_path = engine.save_recording(str(tmp_path), "ADALM-Pluto")
    meta_json = json.loads(
        open(base_path + ".sigmf-meta", encoding="utf-8").read()
    )
    assert meta_json["global"]["pluto_siege:rx_gain"] == cfg.rx_gain
    assert meta_json["global"]["pluto_siege:rf_bandwidth"] == cfg.sample_rate
    label = _label_for_recording(base_path + ".sigmf-data")
    assert "[" not in label
    assert "samples (" in label and " s)" in label

    # Verify saving with GPS fix attaches geolocation and renders formatted tag
    gps_fix = GPSFix(latitude=54.657, longitude=20.406, altitude=150.0, has_fix=True)
    gps_base = engine.save_recording(str(tmp_path / "gps_rec"), "ADALM-Pluto", gps_fix=gps_fix)
    assert "[54.657°N, 20.406°E]" in _label_for_recording(gps_base + ".sigmf-data")

    payload, sr, freq = TransmitEngine.prepare_transmission(base_path)
    assert sr == cfg.sample_rate
    assert freq == cfg.rx_freq

    import pytest as _pytest

    with _pytest.raises(ValueError, match="RX frequency"):
        cfg_rx(sdr, freq=69_999_999, config=cfg)
    with _pytest.raises(ValueError, match="TX frequency"):
        cfg_tx(sdr, freq=6_000_000_001, config=cfg)

    timeout_ok = TransmitEngine.transmit(sdr, payload, sr, freq, config=cfg)
    assert timeout_ok is True
    assert sdr.last_tx is not None
    assert sdr.last_tx.size == payload.size


def test_loopback_tester_passes_on_clean_loopback() -> None:
    sdr = FakeSDR()
    cfg = AppConfig(sample_rate=2_100_000, rx_buffer_size=1024)
    snr_db, tone_freq, timeout_ok = LoopbackTester.run_test(
        sdr, cfg.sample_rate, cfg.rx_buffer_size, config=cfg
    )
    assert timeout_ok is True
    assert tone_freq > 0
    assert snr_db > 30.0


def test_parse_number_supports_unit_suffixes_and_mhz_shorthand() -> None:
    assert parse_number("433.92M", int, 1, None) == 433_920_000
    assert parse_number("433.92", int, 1, None) == 433_920_000
    assert parse_number("433", int, 1, None) == 433_000_000
    assert parse_number("2.1MSPS", int, 521_000, 61_440_000) == 2_100_000
    assert parse_number("-15.5", float, -89.75, 0.0) == -15.5


def test_sound_wav_synthesis_and_enum_aliases() -> None:
    assert SoundCue.PASS is SoundCue.SUCCESS
    assert SoundCue.CAPTURE_SUCCESS is SoundCue.SUCCESS
    assert SoundCue.TX_COMPLETE is SoundCue.SUCCESS
    assert SoundCue.TX_START is SoundCue.START

    wav_bytes = _generate_wav(_CUES[SoundCue.SUCCESS])
    assert wav_bytes.startswith(b"RIFF")
    assert len(wav_bytes) > 1000


def test_device_timeout_and_cleanup_silences_tx_before_destroying_buffers() -> None:
    sdr = FakeSDR()
    assert set_io_timeout(sdr, 0.05) is True
    assert sdr.ctx.timeout_ms == 5000
    sdr.loopback = 1
    sdr.tx_cyclic_buffer = True
    sdr.events.clear()
    cleanup_sdr(sdr)
    assert sdr.loopback == 0
    assert sdr.tx_cyclic_buffer is False
    assert sdr.tx_hardwaregain_chan0 == -89.75
    assert sdr.events == [
        "set:loopback=0",
        "set:tx_cyclic_buffer=False",
        "set:tx_hardwaregain_chan0=-89.75",
        "tx_destroy_buffer",
        "rx_destroy_buffer",
    ]
    release_sdr(sdr)
    assert sdr.ctx is None


def test_v120_compatibility_aliases_and_sigmf_multi_capture(tmp_path) -> None:
    assert max_subwindow_dbfs is peak_window_dbfs
    assert hide_cursor is set_cursor_visible
    assert isinstance(validate_settings({"rx_freq": 433_920_000}), dict)
    assert validate_settings({"rx_freq": 433_920_000})["rx_freq"] == 433_920_000

    base = str(tmp_path / "overwrite_test")
    iq1 = np.array([10.0 + 20.0j, 30.0 + 40.0j], dtype=np.complex64)
    iq2 = np.array([50.0 + 60.0j, 70.0 + 80.0j], dtype=np.complex64)
    save_sigmf_pair(
        base,
        iq1,
        freq=433_920_000,
        sr=2_100_000,
        timestamp_iso="2026-01-01T12:00:00Z",
        hw_model="ADALM-Pluto",
    )
    # Atomic overwrite must succeed as in v1.2.0
    save_sigmf_pair(
        base,
        iq2,
        freq=868_000_000,
        sr=2_100_000,
        timestamp_iso="2026-01-01T12:05:00Z",
        hw_model="ADALM-Pluto",
    )
    sr, freq, error = load_sigmf_meta(base)
    assert error is None
    assert sr == 2_100_000
    assert freq == 868_000_000

    meta_json = json.loads(
        (tmp_path / "overwrite_test.sigmf-meta").read_text(encoding="utf-8")
    )
    assert meta_json["captures"][0]["core:frequency"] == 868_000_000


def test_ui_chrome_renders_footer_separator_and_small_window_notice() -> None:
    win = FakeWindow()
    start_row = draw_chrome(
        cast(Any, win), "Main Menu", "↑↓ = move ▎ Enter = select ▎ Q = quit"
    )
    assert start_row == 12
    rendered_texts = [text for _, _, text, _ in win.calls]
    assert "▎" in rendered_texts

    small_win = FakeWindow(height=20, width=70)
    assert draw_chrome(cast(Any, small_win), "Capture Mode", "Esc = cancel") == -1
    small_texts = " ".join(text for _, _, text, _ in small_win.calls)
    assert "Minimum size required: 110x39" in small_texts
    assert "Esc = cancel" in small_texts
    assert brief("hello\nworld\x07", 20) == "hello world"


def test_ui_framework_navigation_and_dialogs() -> None:
    win = FakeWindow()
    items = ["Capture", "Transmit", "Loopback Test", "Settings", "Exit"]

    # 1. Quick selection via numeric keys and letters
    win.keys = [ord("1")]
    assert menu_select(cast(Any, win), "Main Menu", items, hotkeys=True) == 0
    win.keys = [ord("T")]
    assert menu_select(cast(Any, win), "Main Menu", items, hotkeys=True) == 1

    # 2. Unified cancel / exit via Q or Esc
    win.keys = [ord("q")]
    assert menu_select(cast(Any, win), "Main Menu", items) is None
    win.keys = [ord("Q")]
    assert scroll_view(cast(Any, win), "Log", [("line", 0)]) in (ord("q"), ord("Q"))

    # 3. Confirmation dialog selection toggle
    win.keys = [ord(" "), 10]
    assert confirm_dialog(cast(Any, win), "Test", "Proceed?") is False

    # 4. Status widgets tag rendering
    win.calls.clear()
    draw_gps_status(cast(Any, win), 10, "54.657°N, 20.406°E (3 sats)", has_fix=True)
    draw_gps_status(cast(Any, win), 11, "Searching (3 sats)", has_fix=False)
    rendered = " ".join(text for _, _, text, _ in win.calls)
    assert "[FIX]" in rendered and "[NO FIX]" in rendered


def test_settings_screen_non_blocking_flash_preserves_next_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "settings.json"))
    win = FakeWindow()
    cfg = AppConfig()
    # Press 's' to save and immediately 'q' to exit without losing 'q'
    win.keys = [ord("s"), ord("q")]
    screen_settings(cast(Any, win), config=cfg)
    assert win.keys == []
    assert (tmp_path / "settings.json").exists()

    # Exact minimum terminal size (110x39) fits all 23 settings rows without scrolling
    min_win = FakeWindow(height=39, width=110)
    min_win.keys = [ord("q")]
    screen_settings(cast(Any, min_win), config=AppConfig(enable_sound=False))
    rendered_settings = " ".join(text for _, _, text, _ in min_win.calls)
    assert "Pluto URI" in rendered_settings
    assert "Enable mouse" in rendered_settings


def test_screen_capture_controller_saves_sigmf_and_handles_abort(tmp_path) -> None:
    rec_dir = tmp_path / "records"

    quiet = np.ones(1024, dtype=np.complex64) * (1.0 + 1.0j)
    burst = np.ones(1024, dtype=np.complex64) * (800.0 + 600.0j)
    sdr = FakeSDR(rx_queue=[quiet] * 16 + [burst, burst] + [quiet] * 8)
    cfg = AppConfig(
        rx_buffer_size=1024,
        pre_trigger_buffers=2,
        silence_seconds=0.1,
        max_post_trigger_seconds=0.5,
        enable_sound=False,
        records_dir=str(rec_dir),
    )
    win = FakeWindow()
    win.keys = [ord("q")]
    screen_capture(cast(Any, win), sdr, "ADALM-Pluto", config=cfg)

    assert len(list(rec_dir.glob("*.sigmf-data"))) == 1
    assert len(list(rec_dir.glob("*.sigmf-meta"))) == 1
    rendered = " ".join(text for _, _, text, _ in win.calls)
    assert "RX:" in rendered and "Gain:" in rendered and "Threshold:" in rendered
    assert "samples," in rendered
    assert "Key captured successfully!" in rendered

    # Verify abort path during listening (-1 ends pre-capture flush_input, 'q' aborts capture)
    sdr_abort = FakeSDR(rx_queue=[quiet] * 24)
    win.calls.clear()
    win.nodelay_keys = [-1, ord("q")]
    win.keys = [ord("q")]
    screen_capture(cast(Any, win), sdr_abort, "ADALM-Pluto", config=cfg)
    abort_rendered = " ".join(text for _, _, text, _ in win.calls)
    assert "Capture aborted." in abort_rendered


def test_screen_capture_auto_capture_setting_records_multiple_bursts_until_stopped(
    tmp_path,
) -> None:
    rec_dir = tmp_path / "auto_records"

    quiet = np.ones(1024, dtype=np.complex64) * (1.0 + 1.0j)
    burst = np.ones(1024, dtype=np.complex64) * (800.0 + 600.0j)
    # 13 buffers for prepare(), then two triggered bursts each followed by 206 quiet buffers
    rx_queue = (
        [quiet] * 13
        + [quiet, quiet, burst]
        + [quiet] * 206
        + [quiet, quiet, burst]
        + [quiet] * 206
    )
    sdr = FakeSDR(rx_queue=rx_queue)
    cfg = AppConfig(
        rx_buffer_size=1024,
        auto_capture=True,
        pre_trigger_buffers=2,
        silence_seconds=0.1,
        max_post_trigger_seconds=0.5,
        enable_sound=False,
        records_dir=str(rec_dir),
    )
    win = FakeWindow()
    win.nodelay_keys = [-1] + [-1] * 209 + [-1] * 209 + [ord("q")]
    win.keys = [ord("q")]
    screen_capture(cast(Any, win), sdr, "ADALM-Pluto", config=cfg)

    assert len(list(rec_dir.glob("*.sigmf-data"))) == 2
    assert len(list(rec_dir.glob("*.sigmf-meta"))) == 2
    rendered = " ".join(text for _, _, text, _ in win.calls)
    assert "Capture stopped. Total captured: 2" in rendered
    assert "[#1] Saved:" in rendered
    assert "[#2] Saved:" in rendered


def test_screen_transmit_controller_replays_and_deletes_recording(tmp_path) -> None:
    rec_dir = tmp_path / "records"
    rec_dir.mkdir()

    base = str(rec_dir / "rec_20260101_120000_433MHz")
    iq = np.ones(1024, dtype=np.complex64) * (100.0 + 100.0j)
    save_sigmf_pair(
        base,
        iq,
        freq=433_920_000,
        sr=2_100_000,
        timestamp_iso="2026-01-01T12:00:00Z",
        hw_model="ADALM-Pluto",
        gps_fix=GPSFix(latitude=12.345, longitude=67.89, altitude=42.0, has_fix=True),
    )

    sdr = FakeSDR()
    cfg = AppConfig(enable_sound=False, records_dir=str(rec_dir))
    win = FakeWindow()
    # Enter = transmit -> q = close log -> d = delete -> Enter = Yes -> Enter = close notice
    win.keys = [10, ord("q"), ord("d"), 10, 10]
    screen_transmit(cast(Any, win), sdr, config=cfg)

    assert sdr.last_tx is not None
    assert sdr.last_tx.size == 1024
    assert list(rec_dir.glob("*.sigmf-*")) == []
    assert win.keys == []


def test_screen_settings_controller_edits_steps_and_discards_unsaved(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "settings.json"))
    win = FakeWindow()
    cfg = AppConfig(enable_sound=False, enable_mouse=False)

    # 1. 'l' on row 0 (pluto_uri) -> switches to ip:192.168.2.1
    # 2. 'j' -> moves to row 1 (rx_freq)
    # 3. Enter (10) -> opens edit_number, 9 backspaces + "434.5M" + Enter -> 434_500_000
    # 4. 'q', Space, Enter -> Unsaved Changes dialog, chooses 'No' (stays in settings)
    # 5. 's' -> saves current settings (434_500_000 and ip:192.168.2.1)
    # 6. 'h' -> steps rx_freq down by 10 kHz to 434_490_000 (unsaved)
    # 7. 'q', Enter -> Unsaved Changes dialog, chooses 'Yes' (reverts to 434_500_000)
    win.keys = (
        [ord("l"), ord("j"), 10]
        + [8] * 9
        + [ord(c) for c in "434.5M"]
        + [10, ord("q"), ord(" "), 10, ord("s"), ord("h"), ord("q"), 10]
    )
    screen_settings(cast(Any, win), config=cfg)

    assert win.keys == []
    assert cfg.pluto_uri == "ip:192.168.2.1"
    assert cfg.rx_freq == 434_500_000


def test_headless_capture_and_transmit_latest(tmp_path, monkeypatch) -> None:
    rec_dir = tmp_path / "headless_recs"
    quiet = np.ones(1024, dtype=np.complex64) * (1.0 + 1.0j)
    loud = np.ones(1024, dtype=np.complex64) * (800.0 + 800.0j)
    sdr = FakeSDR(rx_queue=[quiet] * 13 + [quiet, quiet, loud] + [quiet] * 60)

    monkeypatch.setattr(cli, "open_sdr", lambda uri, model="PlutoSDR": (sdr, "ADALM-Pluto"))
    cfg = AppConfig(
        sample_rate=521_000,
        rx_buffer_size=1024,
        pre_trigger_buffers=2,
        silence_seconds=0.1,
        max_post_trigger_seconds=0.5,
        enable_sound=False,
        enable_gps=False,
        records_dir=str(rec_dir),
    )

    cap_opts = cli.CLIOptions(mode="capture", headless=True)
    cap_out = io.StringIO()
    with contextlib.redirect_stdout(cap_out):
        assert cli.run_headless(cap_opts, config=cfg) == 0
    cap_text = cap_out.getvalue()
    assert "Connected: PlutoSDR (usb:)" in cap_text
    assert "ADALM-Pluto" not in cap_text
    assert "[#1]" not in cap_text
    assert "Dir=" not in cap_text
    assert "RX:" in cap_text and "Threshold:" in cap_text and "samples," in cap_text
    saved_files = list(rec_dir.glob("*.sigmf-data"))
    assert len(saved_files) == 1

    tx_opts = cli.CLIOptions(mode="transmit", transmit_path="latest", headless=True)
    tx_out = io.StringIO()
    with contextlib.redirect_stdout(tx_out):
        assert cli.run_headless(tx_opts, config=cfg) == 0
    tx_text = tx_out.getvalue()
    assert "TX:" in tx_text and "File:" in tx_text and "samples," in tx_text
    assert sdr.last_tx is not None
    assert sdr.last_tx.size > 0

    # When enable_gps=True, headless capture prints one initial GPS status line
    class FakeGPSMgr:
        def get_status_text(self) -> str:
            return "Searching (4 sats)"

        def get_latest_fix(self) -> GPSFix:
            return GPSFix(has_fix=False, satellites=4)

    monkeypatch.setattr(cli, "init_gps", lambda cfg: FakeGPSMgr())
    monkeypatch.setattr(cli, "get_gps_manager", lambda: FakeGPSMgr())
    gps_sdr = FakeSDR(rx_queue=[quiet] * 13 + [quiet, quiet, loud] + [quiet] * 60)
    monkeypatch.setattr(cli, "open_sdr", lambda uri, model="PlutoSDR": (gps_sdr, "PlutoSDR"))
    gps_cfg = AppConfig(
        sample_rate=521_000,
        rx_buffer_size=1024,
        pre_trigger_buffers=2,
        silence_seconds=0.1,
        max_post_trigger_seconds=0.5,
        enable_sound=False,
        enable_gps=True,
        records_dir=str(rec_dir),
    )
    gps_out = io.StringIO()
    with contextlib.redirect_stdout(gps_out):
        assert cli.run_headless(cap_opts, config=gps_cfg) == 0
    assert "GPS: [NO FIX] Searching (4 sats)" in gps_out.getvalue()
    assert "\r\033[KSignal:" not in gps_out.getvalue()

    # On a real TTY, headless capture draws the live Signal meter bar and clears before logging
    tty_sdr = FakeSDR(rx_queue=[quiet] * 13 + [quiet, quiet, loud] + [quiet] * 60)
    monkeypatch.setattr(cli, "open_sdr", lambda uri, model="PlutoSDR": (tty_sdr, "PlutoSDR"))
    monkeypatch.setattr(cli, "_is_tty", lambda _s=None: True)
    tty_out = io.StringIO()
    with contextlib.redirect_stdout(tty_out):
        assert cli.run_headless(cap_opts, config=cfg) == 0
    tty_text = tty_out.getvalue()
    assert "Signal: [" in tty_text
    assert "dBFS   thr " in tty_text
    assert "\r\033[K" in tty_text

    # When auto_capture=True, headless loops continuously and numbers [#1], [#2], ...
    auto_rec_dir = tmp_path / "headless_auto_recs"
    auto_sdr = FakeSDR(
        rx_queue=[quiet] * 13
        + [quiet, quiet, loud]
        + [quiet] * 55
        + [quiet, quiet, loud]
        + [quiet] * 55
    )

    def rx_then_ctrl_c() -> NDArray[np.complex64]:
        if not auto_sdr._rx_queue:
            raise KeyboardInterrupt
        return auto_sdr._rx_queue.pop(0)

    monkeypatch.setattr(auto_sdr, "rx", rx_then_ctrl_c)
    monkeypatch.setattr(cli, "open_sdr", lambda uri, model="PlutoSDR": (auto_sdr, "PlutoSDR"))
    monkeypatch.setattr(cli, "_is_tty", lambda _s=None: False)
    auto_cfg = AppConfig(
        sample_rate=521_000,
        rx_buffer_size=1024,
        auto_capture=True,
        pre_trigger_buffers=2,
        silence_seconds=0.1,
        max_post_trigger_seconds=0.5,
        enable_sound=False,
        enable_gps=False,
        records_dir=str(auto_rec_dir),
    )
    auto_out = io.StringIO()
    with contextlib.redirect_stdout(auto_out):
        assert cli.run_headless(cli.CLIOptions(mode=None, headless=True), config=auto_cfg) == 0
    auto_text = auto_out.getvalue()
    assert "[#1] Saved " in auto_text
    assert "[#2] Saved " in auto_text
    assert "Stopped. Total recordings saved: 2." in auto_text
    assert len(list(auto_rec_dir.glob("*.sigmf-data"))) == 2


def test_rf_bandwidth_hardware_programming_and_settings_ui(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "settings.json"))
    sdr = FakeSDR()

    # Auto (0) follows sample_rate up to 56 MHz (no longer capped at 20 MHz)
    cfg_rx(sdr, config=AppConfig(sample_rate=30_000_000, rf_bandwidth=0))
    assert sdr.rx_rf_bandwidth == 30_000_000
    cfg_rx(sdr, config=AppConfig(sample_rate=61_440_000, rf_bandwidth=0))
    assert sdr.rx_rf_bandwidth == 56_000_000

    # Explicit narrowband filter overrides sample_rate on RX and TX
    narrow_cfg = AppConfig(sample_rate=2_100_000, rf_bandwidth=500_000)
    cfg_rx(sdr, config=narrow_cfg)
    assert sdr.rx_rf_bandwidth == 500_000
    cfg_tx(sdr, config=narrow_cfg)
    assert sdr.tx_rf_bandwidth == 500_000

    # TUI Settings row 3 (rf_bandwidth): step right twice (0 -> 200k -> 250k),
    # step left twice (250k -> 200k -> 0), then Enter "350k", save, and exit.
    ui_cfg = AppConfig(enable_sound=False, enable_mouse=False)
    win = FakeWindow()
    win.keys = (
        [ord("j")] * 3
        + [ord("l"), ord("l"), ord("h"), ord("h"), 10, 8]
        + [ord(c) for c in "350k"]
        + [10, ord("s"), ord("q")]
    )
    screen_settings(cast(Any, win), config=ui_cfg)
    assert win.keys == []
    assert ui_cfg.rf_bandwidth == 350_000


def test_min_burst_filter_rejects_short_spikes_and_tx_repeats() -> None:
    quiet = np.ones(1024, dtype=np.complex64) * (1.0 + 1.0j)
    burst = np.ones(1024, dtype=np.complex64) * (800.0 + 600.0j)

    # At 521 kSPS, 1024 samples = ~1.96 ms.
    # First spike is 1 buffer (~1.96 ms < 8 ms) -> rejected without firing trigger!
    # Second burst is 6 buffers (~11.8 ms >= 8 ms) -> accepted and captured!
    rx_queue = (
        [quiet] * 14
        + [quiet, quiet, burst]
        + [quiet] * 55
        + [burst] * 6
        + [quiet] * 55
    )
    sdr = FakeSDR(rx_queue=rx_queue)
    cfg = AppConfig(
        sample_rate=521_000,
        rx_buffer_size=1024,
        pre_trigger_buffers=2,
        min_burst_seconds=0.008,
        silence_seconds=0.1,
        max_post_trigger_seconds=0.5,
        tx_repeats=3,
    )
    engine = CaptureEngine(sdr, config=cfg)
    engine.prepare()
    triggers = 0

    def on_trigger() -> None:
        nonlocal triggers
        triggers += 1

    engine.listen_and_capture(
        check_abort=lambda: False,
        on_trigger_detected=on_trigger,
    )

    assert triggers == 1
    assert engine.captured_data is not None
    # Expected: 2 pre-trigger + 6 active burst + 1 trailing quiet buffer = 9 * 1024
    assert engine.captured_data.size == 9 * 1024

    # Verify tx_repeats=3 transmits payload 3 times
    tx_sdr = FakeSDR()
    payload = np.ones(512, dtype=np.complex64) * (100.0 + 100.0j)
    assert TransmitEngine.transmit(tx_sdr, payload, 521_000, 433_920_000, config=cfg)
    assert tx_sdr.tx_count == 3


def test_chunked_large_tx_and_single_burst_abort(tmp_path) -> None:
    tx_sdr = FakeSDR()
    large_payload = np.ones(TX_CHUNK_SAMPLES + 128, dtype=np.complex64) * (50.0 + 50.0j)
    cfg = AppConfig(tx_repeats=1, enable_sound=False)
    assert TransmitEngine.transmit(
        tx_sdr, large_payload, 50_000_000, 433_920_000, config=cfg
    )
    assert tx_sdr.tx_count == 2
    assert tx_sdr.last_tx is not None
    assert tx_sdr.last_tx.size == TX_CHUNK_SAMPLES

    # Single-burst abort in TUI shows "Transmit Aborted"
    base = str(tmp_path / "rec_20260101_120000_433MHz")
    save_sigmf_pair(
        base,
        np.ones(2048, dtype=np.complex64) * 100.0,
        freq=433_920_000,
        sr=2_100_000,
        timestamp_iso="2026-01-01T12:00:00Z",
        hw_model="ADALM-Pluto",
    )
    win = FakeWindow()
    win.nodelay_keys = [-1, ord("q")]
    win.keys = [ord("q")]
    do_transmit(cast(Any, win), FakeSDR(), base + ".sigmf-data", config=cfg)
    rendered = " ".join(text for _, _, text, _ in win.calls)
    assert "Transmit Aborted" in rendered
    assert "Transmission aborted." in rendered


def test_connect_screen_menu_retry_and_settings_tilde_expansion(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(main_menu.time, "sleep", lambda _s: None)
    attempts = 0
    fake_sdr = FakeSDR()

    def fake_open(uri: str, model: str = "PlutoSDR") -> tuple[FakeSDR, str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("No IIO context found")
        return fake_sdr, "ADALM-Pluto"

    monkeypatch.setattr(main_menu, "open_sdr", fake_open)
    win = FakeWindow()
    win.keys = [ord("r")]
    connected_sdr, model = main_menu.connect_screen(
        cast(Any, win), config=AppConfig(enable_gps=False)
    )
    assert connected_sdr is fake_sdr
    assert model == "ADALM-Pluto"
    assert attempts == 2
    main_menu._set_active_sdr(None)

    # Tilde expansion in Settings "Records dir" (row 20) does not flash false warning
    ui_cfg = AppConfig(enable_sound=False, enable_mouse=False, records_dir="/tmp/old")
    win_s = FakeWindow()
    win_s.keys = (
        [ord("j")] * 20
        + [10]
        + [8] * 8
        + [ord(c) for c in "~/captures"]
        + [10, ord("s"), ord("q")]
    )
    screen_settings(cast(Any, win_s), config=ui_cfg)
    assert ui_cfg.records_dir == os.path.expanduser("~/captures")
    rendered_s = " ".join(text for _, _, text, _ in win_s.calls)
    assert "Out of range" not in rendered_s


def test_auto_recal_seconds_adapts_downward_and_upward(tmp_path, monkeypatch) -> None:
    import pluto_siege.engine as engine_mod
    from pluto_siege.device import format_capture_summary

    fake_clock = [100.0]
    monkeypatch.setattr(engine_mod.time, "monotonic", lambda: fake_clock[0])

    noisy = np.ones(1024, dtype=np.complex64) * (20.0 + 20.0j)
    quiet = np.ones(1024, dtype=np.complex64) * (0.5 + 0.5j)
    weak_burst = np.ones(1024, dtype=np.complex64) * (6.0 + 6.0j)
    strong_burst = np.ones(1024, dtype=np.complex64) * (400.0 + 400.0j)

    # 1) Downward adaptation: prepare() sees noisy environment (~-37 dBFS, thr ~-29 dBFS),
    # then listener moves to quiet environment (~-75 dBFS) -> recalibrates downward in-stream
    # and catches weak_burst (~-53.5 dBFS) that would have been missed before!
    rx_down = [noisy] * 13 + [quiet] * 14 + [weak_burst, weak_burst] + [quiet] * 10
    sdr_down = FakeSDR(rx_queue=rx_down)
    cfg = AppConfig(
        sample_rate=521_000,
        rx_buffer_size=1024,
        pre_trigger_buffers=2,
        silence_seconds=0.01,
        max_post_trigger_seconds=0.1,
        auto_recal_seconds=10.0,
    )
    eng_down = CaptureEngine(sdr_down, config=cfg)
    eng_down.prepare()
    initial_thr = eng_down.trigger_threshold
    _, summary_line = format_capture_summary(
        eng_down.actual_freq,
        eng_down.actual_sr,
        eng_down.trigger_threshold,
        eng_down.noise_floor,
        cfg,
    )
    assert "recal 10s" in summary_line

    recal_events: list[tuple[float, float]] = []

    def tick_clock(_lvl: float, _sat: bool) -> None:
        fake_clock[0] += 1.0

    eng_down.listen_and_capture(
        check_abort=lambda: False,
        on_meter_update=tick_clock,
        on_recalibrated=lambda nf, thr: recal_events.append((nf, thr)),
    )
    assert len(recal_events) == 1
    assert eng_down.trigger_threshold < initial_thr - 20.0
    assert eng_down.captured_data is not None
    assert eng_down.captured_data.size > 0

    # 2) Upward adaptation: prepare() sees quiet environment (~-75 dBFS), then time advances
    # past auto_recal_seconds into a noisy environment (~-37 dBFS). Fallback probe raises
    # threshold above the new noise floor so noisy background is ignored and strong_burst triggers!
    fake_clock[0] = 200.0
    rx_up = [quiet] * 13 + [noisy] * 14 + [strong_burst, strong_burst] + [noisy] * 10
    sdr_up = FakeSDR(rx_queue=rx_up)
    eng_up = CaptureEngine(sdr_up, config=cfg)
    eng_up.prepare()
    quiet_thr = eng_up.trigger_threshold
    fake_clock[0] = 215.0  # 15s elapsed >= 10s auto_recal_seconds

    recal_up: list[tuple[float, float]] = []
    eng_up.listen_and_capture(
        check_abort=lambda: False,
        on_recalibrated=lambda nf, thr: recal_up.append((nf, thr)),
    )
    assert len(recal_up) == 1
    assert eng_up.trigger_threshold > quiet_thr + 20.0
    assert eng_up.captured_data is not None
    assert not eng_up.is_partial

    # 3) TUI Settings row 7 (auto_capture) & row 10 (auto_recal_seconds)
    ui_cfg = AppConfig(enable_sound=False, enable_mouse=False)
    assert ui_cfg.auto_capture is False
    assert ui_cfg.auto_recal_seconds == 0.0
    win_save = FakeWindow()
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "settings.json"))
    win_save.keys = (
        [ord("j")] * 7
        + [ord(" ")]
        + [ord("j")] * 3
        + [ord("l"), ord("l"), ord("h"), ord("s"), ord("q")]
    )
    screen_settings(cast(Any, win_save), config=ui_cfg)
    assert ui_cfg.auto_capture is True
    assert ui_cfg.auto_recal_seconds == 5.0

    # 4) TUI draw_signal_meter and headless meter show temporary '(recal)' next to 'thr'
    #    on every recalibration without logging lines even when threshold delta is 0 dB.
    from pluto_siege.ui.widgets.framework import draw_signal_meter

    meter_win = FakeWindow()
    draw_signal_meter(cast(Any, meter_win), 15, -60.0, -52.0, False, recalibrating=True)
    assert "(recal)" in " ".join(text for _, _, text, _ in meter_win.calls)
    meter_win.calls.clear()
    draw_signal_meter(cast(Any, meter_win), 15, -60.0, -52.0, False, recalibrating=False)
    assert "(recal)" not in " ".join(text for _, _, text, _ in meter_win.calls)

    # Headless TTY run with constant quiet floor (0 dB change): shows (recal) temporarily,
    # then expires after RECAL_INDICATOR_SECONDS, and adds zero recalibration lines to stdout log.
    hl_clock = [500.0]
    monkeypatch.setattr(engine_mod.time, "monotonic", lambda: hl_clock[0])

    flat_sdr_rx = [quiet] * 13 + [quiet] * 18 + [weak_burst, weak_burst] + [quiet] * 10
    hl_sdr = FakeSDR(rx_queue=flat_sdr_rx)

    def advance_on_rx() -> NDArray[np.complex64]:
        hl_clock[0] += 0.6
        return FakeSDR.rx(hl_sdr)

    monkeypatch.setattr(hl_sdr, "rx", advance_on_rx)
    monkeypatch.setattr(cli, "open_sdr", lambda uri, model="PlutoSDR": (hl_sdr, "PlutoSDR"))
    monkeypatch.setattr(cli, "_is_tty", lambda _s=None: True)

    hl_cfg = AppConfig(
        sample_rate=521_000,
        rx_buffer_size=1024,
        pre_trigger_buffers=2,
        silence_seconds=0.05,
        max_post_trigger_seconds=0.2,
        auto_recal_seconds=5.0,
        enable_sound=False,
        enable_gps=False,
        records_dir=str(tmp_path / "hl_recs"),
    )
    hl_out = io.StringIO()
    with contextlib.redirect_stdout(hl_out):
        assert cli.run_headless(cli.CLIOptions(mode="capture", headless=True), config=hl_cfg) == 0

    hl_text = hl_out.getvalue()
    assert "Noise floor recalibrated" not in hl_text
    assert "(recal)" in hl_text
    # Later meter updates after the 2s window render without (recal)
    last_meter_line = [ln for ln in hl_text.split("\r\033[K") if "Signal: [" in ln][-1]
    assert "(recal)" not in last_meter_line



