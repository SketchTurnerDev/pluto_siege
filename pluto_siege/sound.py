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

"""Acoustic cue and sound effect synthesis for PlutoSiege."""

from __future__ import annotations

import io
import queue
import shutil
import subprocess
import sys
import threading
import wave
from enum import Enum, auto
from typing import NamedTuple

import numpy as np


class SoundCue(Enum):
    """Categorized audio feedback cues for PlutoSiege events."""

    SUCCESS = auto()
    FAIL = auto()
    START = auto()
    TRIGGER = auto()
    ALERT = auto()

    # Semantic aliases
    PASS = SUCCESS
    CAPTURE_SUCCESS = SUCCESS
    TX_START = START
    TX_COMPLETE = SUCCESS


class Tone(NamedTuple):
    """A single audible frequency pulse."""

    freq_hz: int
    duration_ms: int


_CUES: dict[SoundCue, list[Tone]] = {
    SoundCue.SUCCESS: [Tone(880, 80), Tone(1320, 120)],
    SoundCue.FAIL: [Tone(370, 90), Tone(220, 160)],
    SoundCue.START: [Tone(1100, 60)],
    SoundCue.TRIGGER: [Tone(1480, 70)],
    SoundCue.ALERT: [Tone(550, 70), Tone(550, 70)],
}

_sound_queue: queue.Queue[list[Tone]] = queue.Queue(maxsize=10)
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()


def _generate_wav(tones: list[Tone]) -> bytes:
    """Synthesize an in-memory 16-bit mono PCM WAV from frequency tone definitions."""
    sample_rate = 22050
    segments: list[np.ndarray] = []
    gap = np.zeros(int(sample_rate * 0.015), dtype="<i2")
    for i, tone in enumerate(tones):
        num_samples = int(sample_rate * (tone.duration_ms / 1000.0))
        if num_samples > 0:
            t = np.arange(num_samples, dtype=np.float64)
            wave_pcm = (18000.0 * np.sin(2.0 * np.pi * tone.freq_hz * t / sample_rate)).astype("<i2")
            segments.append(wave_pcm)
        if i < len(tones) - 1:
            segments.append(gap)

    pcm_bytes = np.concatenate(segments).tobytes() if segments else b""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _play_tones(tones: list[Tone]) -> None:
    """Synchronously play a sequence of frequency tones on the audio worker thread."""
    try:
        wav_bytes = _generate_wav(tones)
        for player, args in (
            ("aplay", ["-q", "-"]),
            ("paplay", []),
            ("pw-play", ["-"]),
        ):
            executable = shutil.which(player)
            if executable:
                try:
                    subprocess.run(
                        [executable, *args],
                        input=wav_bytes,
                        timeout=2.0,
                        check=True,
                        shell=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except (subprocess.SubprocessError, OSError):
                    continue
    except Exception:
        pass

    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def _worker() -> None:
    """Background consumer thread that plays tones without blocking SDR processing."""
    while True:
        tones = _sound_queue.get()
        try:
            _play_tones(tones)
        except Exception:
            pass
        finally:
            _sound_queue.task_done()


def play_cue(cue: SoundCue, enabled: bool = True) -> None:
    """Queue acoustic cue for non-blocking playback in the background worker thread."""
    if not enabled or not isinstance(cue, SoundCue):
        return
    tones = _CUES.get(cue)
    if not tones:
        return

    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(
                target=_worker, daemon=True, name="PlutoSiegeAudio"
            )
            _worker_thread.start()

    try:
        _sound_queue.put_nowait(tones)
    except queue.Full:
        pass


__all__ = ["SoundCue", "Tone", "play_cue"]
