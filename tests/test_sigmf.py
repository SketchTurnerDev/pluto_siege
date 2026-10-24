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

import json

import numpy as np

from pluto_siege.gps import GPSFix
from pluto_siege.sigmf import (
    load_sigmf_geolocation,
    load_sigmf_meta,
    save_sigmf_pair,
)


def test_sigmf_round_trip_with_geolocation(tmp_path) -> None:
    base = str(tmp_path / "capture")
    samples = np.array([1 + 2j, -3 + 4j], dtype=np.complex64)
    gps_fix = GPSFix(
        longitude=37.6173,
        latitude=55.7558,
        altitude=180.5,
        has_fix=True,
    )

    save_sigmf_pair(
        base,
        samples,
        freq=433_920_000,
        sr=2_100_000,
        timestamp_iso="2026-01-01T12:00:00Z",
        hw_model="ADALM-Pluto",
        gps_fix=gps_fix,
    )

    sample_rate, frequency, error = load_sigmf_meta(base)
    assert (sample_rate, frequency, error) == (2_100_000, 433_920_000, None)
    assert load_sigmf_geolocation(base) == (37.6173, 55.7558, 180.5)
    assert (tmp_path / "capture.sigmf-data").stat().st_size == samples.size * 8

    metadata = json.loads((tmp_path / "capture.sigmf-meta").read_text(encoding="utf-8"))
    assert metadata["global"]["core:datatype"] == "cf32_le"
    assert metadata["captures"][0]["core:frequency"] == 433_920_000


def test_sigmf_rejects_frequency_outside_bounds(tmp_path) -> None:
    base = str(tmp_path / "capture")
    save_sigmf_pair(
        base,
        np.ones(4, dtype=np.complex64),
        freq=433_920_000,
        sr=2_100_000,
        timestamp_iso="2026-01-01T12:00:00Z",
        hw_model="ADALM-Pluto",
    )

    sample_rate, frequency, error = load_sigmf_meta(
        base, bounds=(868_000_000, 869_000_000)
    )
    assert sample_rate is None
    assert frequency is None
    assert error is not None


def test_sigmf_rejects_non_finite_samples(tmp_path) -> None:
    try:
        save_sigmf_pair(
            str(tmp_path / "capture"),
            np.array([np.nan + 0j], dtype=np.complex64),
            freq=433_920_000,
            sr=2_100_000,
            timestamp_iso="2026-01-01T12:00:00Z",
            hw_model="ADALM-Pluto",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("non-finite samples must be rejected")


def test_sigmf_omits_invalid_geolocation(tmp_path) -> None:
    base = str(tmp_path / "capture")
    save_sigmf_pair(
        base,
        np.ones(1, dtype=np.complex64),
        freq=433_920_000,
        sr=2_100_000,
        timestamp_iso="2026-01-01T12:00:00Z",
        hw_model="ADALM-Pluto",
        gps_fix=GPSFix(longitude=181.0, latitude=55.0, has_fix=True),
    )

    assert load_sigmf_geolocation(base) is None
