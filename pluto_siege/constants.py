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

"""Hardware limits, detection tuning, and application-wide constants."""

import os

AUTHOR = "SketchTurnerDev"  # recorded in SigMF metadata as core:author
VERSION = "1.0.0"
LICENSE = "GPL-3.0-or-later"
LICENSE_SHORT = "GNU GPLv3"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "settings.json")
RECORDS_DIR = os.path.join(BASE_DIR, "recordings")

# ---------------------------------------------------------------------------
# Hardware limits and sample accounting
# ---------------------------------------------------------------------------

# AD9363 datasheet range, and the wider range the silicon usually tunes to.
FREQ_RANGE_IN_SPEC = (325_000_000, 3_800_000_000)
FREQ_RANGE_EXTENDED = (70_000_000, 6_000_000_000)
# Lower bound is pyadi-iio's own hard limit: its sample_rate setter raises below
# 521 kSPS. It also loads a matching FIR decimation filter on every assignment,
# which is what makes rates under ~2.083 MSPS reachable at all.
SAMPLE_RATE_RANGE = (521_000, 61_440_000)
RX_GAIN_RANGE = (0.0, 74.5)
TX_GAIN_RANGE = (-89.75, 0.0)
RX_BUFFER_RANGE = (1024, 32768)
MAX_RF_BW = 20_000_000

# Stock Pluto delivers 12-bit samples sign-extended into 16-bit words, so full
# scale is 2**11. This is deliberately not a setting: it is a property of the
# hardware, and the old 32768 option silently shifted every dBFS reading by
# 24 dB, which quietly falsifies the level meter and the saturation warning.
RX_FULL_SCALE = 2048.0

# TX is scaled differently from RX, which is an asymmetry of the pyadi-iio API
# rather than a typo here: samples are handed to the DAC as int16, and ADI's own
# examples cap the amplitude at 2**14 to leave headroom in the interpolating TX
# filters. Anything larger risks clipping inside that chain.
TX_DAC_MAX = 2**14 - 1
TX_BACKOFF = 0.8

# Samples are held as complex64 (SigMF "cf32_le"): 2 x float32.
BYTES_PER_SAMPLE = 8
# Headroom for transient copies such as concatenation.
RAM_SAFETY_FACTOR = 2
MAX_RAM_BYTES = 512 * 1024 * 1024
# Ceiling for one TX burst (payload plus gap). 16 Msamples is 128 MB and about
# 8 s at 2 MSPS - far beyond any remote, while still rejecting combinations like
# a 1 s gap at 61.44 MSPS, whose silence alone would be ~470 MB per loop.
MAX_TX_BURST_SAMPLES = 16 * 1024 * 1024
# sdr.tx() returns once the kernel accepts the buffer, not once the samples have
# left the DAC, so the buffer must not be destroyed the instant the loop ends.
TX_DRAIN_MARGIN_SECONDS = 0.05

# Bound blocking libiio transfers. The margin is deliberately generous: the goal
# is to fail instead of hanging forever, not to police a merely slow link.
IO_TIMEOUT_MIN_MS = 5_000
IO_TIMEOUT_FACTOR = 10
IO_TIMEOUT_UNAVAILABLE = "No I/O timeout: a stalled link cannot be interrupted."

# ---------------------------------------------------------------------------
# Detection tuning
# ---------------------------------------------------------------------------

# Sub-window length for the level metric. Noise floor, trigger and release all
# use this granularity, which is what makes the margins below comparable.
DETECT_SUB_WINDOW = 512
NF_PERCENTILE = 25           # noise-floor percentile over pooled sub-windows
NF_PROBE_BUFFERS = 10        # buffers captured to estimate the noise floor
TRIGGER_MARGIN_DB = 8.0      # trigger this far above the noise floor
RELEASE_MARGIN_DB = 3.0      # treat as silent this far above the noise floor
MANUAL_RELEASE_DROP_DB = 5.0  # release this far below a manual threshold
# A lone sub-window above the threshold is a click, not a transmission: the
# smallest RX buffer still holds two sub-windows, so requiring two keeps
# impulse noise from arming a full capture without delaying a real burst.
MIN_TRIGGER_SUB_WINDOWS = 2
RX_FLUSH_BUFFERS = 3         # stale buffers discarded after configuring RX
SATURATION_MARGIN = 0.98     # fraction of full scale counted as clipping
DB_EPSILON = 1e-15           # keeps log10 finite on an all-zero buffer

# ---------------------------------------------------------------------------
# Settings ranges
# ---------------------------------------------------------------------------

PRE_TRIGGER_RANGE = (1, 20)
SILENCE_RANGE = (0.1, 5.0)
POST_TRIGGER_RANGE = (0.5, 10.0)
THRESHOLD_RANGE = (-120.0, 0.0)
AUTO_MARGIN_RANGE = (1.0, 30.0)

# ---------------------------------------------------------------------------
# Loopback test
# ---------------------------------------------------------------------------

LOOPBACK_TONE_HZ = 100_000.0
LOOPBACK_TONE_BINS = 10      # half-width of the band counted as signal
LOOPBACK_TX_AMPLITUDE = 0.6
LOOPBACK_MIN_SNR_DB = 10.0

# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------

LOGO_LINES = [
    r"     ____  __      __       _____ _                ",
    r"    / __ \/ /_  __/ /_____ / ___/(_)__  ____ ____  ",
    r"   / /_/ / / / / / __/ __ \\__ \/ / _ \/ __ `/ _ \ ",
    r"  / ____/ / /_/ / /_/ /_/ /__/ / /  __/ /_/ /  __/ ",
    r" /_/   /_/\__,_/\__/\____/____/_/\___/\__, /\___/  ",
    r"                                     /____/        ",
    f" {AUTHOR} v{VERSION} ({LICENSE_SHORT})",
]
