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

"""PlutoSDR capture and replay tool for short ISM-band bursts.

A full-screen terminal application that records brief RF transmissions - car
remotes, doorbells, weather stations and similar OOK/ASK devices - and replays
them. Recordings are written as SigMF pairs so they can be opened in other
tools as well.

Usage:
    python3 -m pluto_siege

Requires Python 3.9+, an interactive terminal, an ADALM-Pluto (or compatible
AD936x device) and:
    pip install -r requirements.txt

Transmitting is regulated. Replay only signals you are licensed or permitted to
emit, into a dummy load or at the lowest gain that does the job.

Author: SketchTurnerDev
"""

from pluto_siege.constants import AUTHOR, LICENSE, VERSION

__version__ = VERSION
__author__ = AUTHOR
__license__ = LICENSE

__all__ = ["__version__", "__author__", "__license__"]
