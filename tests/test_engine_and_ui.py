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

import json
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from pluto_siege.config import AppConfig
from pluto_siege.constants import brief
from pluto_siege.device import cleanup_sdr, set_io_timeout
from pluto_siege.dsp import max_subwindow_dbfs, peak_window_dbfs
from pluto_siege.engine import CaptureEngine, LoopbackTester, TransmitEngine
from pluto_siege.gps import GPSFix
from pluto_siege.settings import validate_settings
from pluto_siege.sigmf import load_sigmf_meta, save_sigmf_pair
from pluto_siege.sound import _CUES, SoundCue, _generate_wav
from pluto_siege.ui.screens.transmit import _label_for_recording
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

    def rx_destroy_buffer(self) -> None:
        self.events.append("rx_destroy_buffer")

    def tx_destroy_buffer(self) -> None:
        self.events.append("tx_destroy_buffer")


class FakeWindow:
    """Minimal curses.window double for headless UI framework testing."""

    def __init__(self, height: int = 40, width: int = 100) -> None:
        self.height = height
        self.width = width
        self.calls: list[tuple[int, int, str, int]] = []
        self.keys: list[int] = []
        self._nodelay = False

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def getch(self) -> int:
        if self._nodelay:
            return -1
        return self.keys.pop(0) if self.keys else KEY_ESC

    def nodelay(self, flag: bool) -> None:
        self._nodelay = flag

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
    assert "[" not in _label_for_recording(base_path + ".sigmf-data")

    # Verify saving with GPS fix attaches geolocation and renders formatted tag
    gps_fix = GPSFix(latitude=54.657, longitude=20.406, altitude=150.0, has_fix=True)
    gps_base = engine.save_recording(str(tmp_path / "gps_rec"), "ADALM-Pluto", gps_fix=gps_fix)
    assert "[54.657°N, 20.406°E]" in _label_for_recording(gps_base + ".sigmf-data")

    payload, sr, freq = TransmitEngine.prepare_transmission(
        base_path, bounds=cfg.freq_bounds
    )
    assert sr == cfg.sample_rate
    assert freq == cfg.rx_freq

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
    assert parse_number("433.92M", int, 325_000_000, 3_800_000_000) == 433_920_000
    assert parse_number("433.92", int, 325_000_000, 3_800_000_000) == 433_920_000
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

    meta_json = json.loads((tmp_path / "overwrite_test.sigmf-meta").read_text(encoding="utf-8"))
    assert meta_json["captures"][0]["core:frequency"] == 868_000_000


def test_ui_chrome_renders_footer_separator_and_small_window_notice() -> None:
    win = FakeWindow(height=40, width=100)
    start_row = draw_chrome(cast(Any, win), "Main Menu", "↑↓ = move ▎ Enter = select ▎ Q = quit")
    assert start_row == 12
    rendered_texts = [text for _, _, text, _ in win.calls]
    assert "▎" in rendered_texts

    small_win = FakeWindow(height=20, width=70)
    assert draw_chrome(cast(Any, small_win), "Capture Mode", "Esc = cancel") == -1
    small_texts = " ".join(text for _, _, text, _ in small_win.calls)
    assert "Minimum size required: 97x37" in small_texts
    assert "Esc = cancel" in small_texts
    assert brief("hello\nworld\x07", 20) == "hello world"


def test_ui_framework_navigation_and_dialogs() -> None:
    win = FakeWindow(height=40, width=100)
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

