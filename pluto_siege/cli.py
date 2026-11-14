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

"""Command-line interface parser and headless console runner."""

from __future__ import annotations

import argparse
import glob
import inspect
import math
import os
import re
import shutil
import signal
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pluto_siege import config as config_mod
from pluto_siege.config import (
    CONFIG,
    AppConfig,
    normalize_pluto_uri,
    parse_number,
    parse_rf_bandwidth,
    save_settings,
)
from pluto_siege.constants import (
    AUTO_MARGIN_RANGE,
    AUTO_RECAL_RANGE,
    BYTES_PER_SAMPLE,
    FREQ_RANGE,
    IO_TIMEOUT_UNAVAILABLE,
    LOOPBACK_MIN_SNR_DB,
    METER_FLOOR_DBFS,
    METER_REFRESH_SECONDS,
    MIN_BURST_RANGE,
    POST_TRIGGER_RANGE,
    PRE_TRIGGER_RANGE,
    RECAL_INDICATOR_SECONDS,
    RX_BUFFER_RANGE,
    RX_GAIN_RANGE,
    SAMPLE_RATE_RANGE,
    SILENCE_RANGE,
    THRESHOLD_RANGE,
    TX_GAIN_RANGE,
    TX_REPEATS_RANGE,
    VERSION,
)
from pluto_siege.device import (
    cleanup_sdr,
    format_capture_summary,
    format_loopback_summary,
    format_tx_summary,
    open_sdr,
    release_sdr,
    resolve_rf_bandwidth,
)
from pluto_siege.engine import CaptureEngine, LoopbackTester, TransmitEngine
from pluto_siege.gps import classify_gps_status, get_gps_manager, init_gps, stop_gps
from pluto_siege.sigmf import load_sigmf_geolocation, load_sigmf_meta
from pluto_siege.sound import SoundCue, play_cue

_ANSI_RESET = "\033[0m"
_ANSI_HEADING = "\033[1;37m"
_ANSI_CYAN = "\033[36m"
_ANSI_OK = "\033[32m"
_ANSI_WARN = "\033[33m"
_ANSI_ERR = "\033[31m"
_ANSI_ERR_BOLD = "\033[1;31m"
_ANSI_DIM = "\033[2m"

_INLINE_STATUS_ACTIVE = False

_OPT_RE = re.compile(r"--?[A-Za-z0-9][A-Za-z0-9-]*")
_METAVAR_RE = re.compile(r"\b(?:true\|false|[A-Z][A-Z0-9_]*)\b")


def _is_tty(stream: Any = None) -> bool:
    """Return True when stream is attached to an interactive terminal."""
    target = sys.stdout if stream is None else stream
    try:
        return bool(target.isatty())
    except Exception:
        return False


def _supports_color(stream: Any = None) -> bool:
    """Return True when ANSI colors should be written to stream."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return _is_tty(stream)


def _ansi(text: str, code: str, *, color: bool) -> str:
    return f"{code}{text}{_ANSI_RESET}" if color and text else text


def _style_invocation(header: str) -> str:
    """Style option flags (cyan) and parameter metavars (yellow) without bold."""
    parts = [p for p in re.split(r"(\s+|,\s*|\[|\])", header) if p]
    out: list[str] = []
    for part in parts:
        if _OPT_RE.fullmatch(part):
            out.append(f"{_ANSI_CYAN}{part}{_ANSI_RESET}")
        elif _METAVAR_RE.fullmatch(part):
            out.append(f"{_ANSI_WARN}{part}{_ANSI_RESET}")
        else:
            out.append(part)
    return "".join(out)


def _style_help_text(text: str) -> str:
    """Apply TUI-matched colors to formatted argparse help text."""
    lines = text.splitlines(keepends=True)
    in_usage = False
    styled: list[str] = []
    for line in lines:
        eol = "\n" if line.endswith("\n") else ""
        body = line[:-1] if eol else line

        if body.startswith("usage: "):
            in_usage = True
            rest = body[len("usage: ") :]
            rest = _style_invocation(rest.replace("pluto_siege", f"{_ANSI_CYAN}pluto_siege{_ANSI_RESET}", 1))
            styled.append(f"{_ANSI_HEADING}usage:{_ANSI_RESET} {rest}{eol}")
            continue

        if in_usage:
            if not body.strip():
                in_usage = False
                styled.append(line)
            else:
                styled.append(f"{_style_invocation(body)}{eol}")
            continue

        if re.fullmatch(r"\s*[A-Za-z][^:\n]*:", body):
            stripped = body.lstrip(" ")
            indent = body[: len(body) - len(stripped)]
            styled.append(f"{indent}{_ANSI_HEADING}{stripped}{_ANSI_RESET}{eol}")
            continue

        if body.startswith("  -"):
            # Split invocation column from two-space separator + description.
            match = re.match(r"^(  )(\S(?:.*?\S)?)( {2,}.*)$", body)
            if match:
                lead, header, desc = match.groups()
                styled.append(f"{lead}{_style_invocation(header)}{desc}{eol}")
            else:
                lead = "  "
                styled.append(f"{lead}{_style_invocation(body[2:])}{eol}")
            continue

        styled.append(line)
    return "".join(styled)


class _PlutoHelpFormatter(argparse.HelpFormatter):
    """HelpFormatter with aligned columns, bold white headings, and non-bold flags."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=42, width=100)

    def format_help(self) -> str:
        rendered = super().format_help()
        if not _supports_color(sys.stdout):
            return rendered
        return _style_help_text(rendered)


@dataclass
class CLIOptions:
    """Parsed command-line options controlling execution mode and config overrides."""

    mode: str | None = None
    transmit_path: str | None = None
    list_recordings: bool = False
    show_config: bool = False
    headless: bool = False
    save_config: bool = False
    reset_config: bool = False
    config_overrides: dict[str, Any] = field(default_factory=dict)


def _parse_bool(raw: str) -> bool:
    """Parse boolean CLI argument ('true' or 'false')."""
    val = raw.strip().lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(
        f"expected 'true' or 'false' (got {raw!r})"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser for PlutoSiege."""
    parser_kwargs: dict[str, Any] = {
        "prog": "pluto_siege",
        "description": "PlutoSiege - ISM-band RF capture and replay tool for PlutoSDR.",
        "add_help": False,
        "allow_abbrev": False,
        "formatter_class": _PlutoHelpFormatter,
    }
    if "color" in inspect.signature(argparse.ArgumentParser.__init__).parameters:
        parser_kwargs["color"] = False
    parser = argparse.ArgumentParser(**parser_kwargs)
    parser.add_argument(
        "--help",
        "-h",
        action="help",
        help="Show help message and exit.",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"PlutoSiege v{VERSION}",
        help="Show program version and exit.",
    )

    mode_group = parser.add_argument_group("Operating modes (mutually exclusive)")
    modes = mode_group.add_mutually_exclusive_group()
    modes.add_argument(
        "--capture",
        "-c",
        dest="capture",
        action="store_true",
        help="Start in Capture mode.",
    )
    modes.add_argument(
        "--transmit",
        "--tx",
        dest="transmit",
        nargs="?",
        const="",
        default=None,
        metavar="FILE",
        help=(
            "Start in Transmit mode (pass a recording # from --list, "
            ".sigmf-data path, 'latest', or omit FILE in TUI)."
        ),
    )
    modes.add_argument(
        "--loopback",
        "--lb",
        dest="loopback",
        action="store_true",
        help="Run the internal digital Loopback Test.",
    )

    exec_group = parser.add_argument_group("CLI execution options")
    exec_group.add_argument(
        "--list",
        "-l",
        dest="list_recordings",
        action="store_true",
        help="List available SigMF recordings in records-dir and exit.",
    )
    exec_group.add_argument(
        "--show-config",
        "--sc",
        dest="show_config",
        action="store_true",
        help="Show current configuration and exit.",
    )
    exec_group.add_argument(
        "--headless",
        "--hl",
        dest="headless",
        action="store_true",
        help=(
            "Run in console text mode without the curses TUI "
            "(outputs plain logs without colors or live meter when redirected to a file/pipe)."
        ),
    )
    exec_group.add_argument(
        "--save",
        "-s",
        dest="save_config",
        action="store_true",
        help="Persist CLI setting changes to settings.json.",
    )
    exec_group.add_argument(
        "--reset",
        "-r",
        dest="reset_config",
        action="store_true",
        help="Reset configuration to defaults and save to settings.json.",
    )

    radio_group = parser.add_argument_group("Radio & connection settings")
    radio_group.add_argument(
        "--pluto-uri",
        "--pu",
        dest="pluto_uri",
        metavar="URI",
        help=(
            "PlutoSDR libiio context URI, e.g. 'usb:', 'usb:1.4.5', "
            "'ip:192.168.2.1', or 'local:' (default: usb:)."
        ),
    )
    radio_group.add_argument(
        "--rx-freq",
        "--rf",
        dest="rx_freq",
        metavar="HZ",
        help="Receiver center frequency in Hz; supports k/M/G suffixes (70M - 6G, default: 433.92M).",
    )
    radio_group.add_argument(
        "--sample-rate",
        "--sr",
        dest="sample_rate",
        metavar="SPS",
        help="Sample rate in SPS; supports k/M suffixes (521k - 61.44M, default: 2.1M).",
    )
    radio_group.add_argument(
        "--rf-bandwidth",
        "--rb",
        dest="rf_bandwidth",
        metavar="HZ",
        help=(
            "Analog RF filter bandwidth in Hz, or 0/'auto' to follow sample-rate; "
            "supports k/M suffixes (0 or 200k - 56M, default: 0)."
        ),
    )
    radio_group.add_argument(
        "--rx-gain",
        "--rg",
        dest="rx_gain",
        metavar="DB",
        help=f"Manual RX hardware gain in dB ({RX_GAIN_RANGE[0]} .. {RX_GAIN_RANGE[1]}, default: 15.0).",
    )
    radio_group.add_argument(
        "--tx-gain",
        "--tg",
        dest="tx_gain",
        metavar="DB",
        help=f"Manual TX hardware gain in dB ({TX_GAIN_RANGE[0]} .. {TX_GAIN_RANGE[1]}, default: 0.0).",
    )
    radio_group.add_argument(
        "--tx-repeats",
        "--tr",
        dest="tx_repeats",
        metavar="N",
        help=f"Number of consecutive burst transmissions per replay ({TX_REPEATS_RANGE[0]} .. {TX_REPEATS_RANGE[1]}, default: 1).",
    )

    trig_group = parser.add_argument_group("Capture engine & trigger settings")
    trig_group.add_argument(
        "--auto-capture",
        "--ac",
        dest="auto_capture",
        type=_parse_bool,
        default=None,
        metavar="true|false",
        help="Enable continuous multi-burst capture loop (default: false).",
    )
    trig_group.add_argument(
        "--auto-threshold",
        "--at",
        dest="auto_threshold",
        type=_parse_bool,
        default=None,
        metavar="true|false",
        help="Enable automatic noise floor threshold detection (default: true).",
    )
    trig_group.add_argument(
        "--auto-trigger-margin",
        "--atm",
        dest="auto_trigger_margin",
        metavar="DB",
        help=(
            f"Trigger margin above noise floor in dB; unused when auto-threshold is false "
            f"({AUTO_MARGIN_RANGE[0]} .. {AUTO_MARGIN_RANGE[1]}, default: 8.0)."
        ),
    )
    trig_group.add_argument(
        "--auto-recal-seconds",
        "--ars",
        dest="auto_recal_seconds",
        metavar="SEC",
        help=(
            f"Periodic noise floor recalibration interval in seconds while listening, "
            f"or 0 to disable; unused when auto-threshold is false; supports ms/s suffixes "
            f"({AUTO_RECAL_RANGE[0]} .. {AUTO_RECAL_RANGE[1]}, default: 0.0)."
        ),
    )
    trig_group.add_argument(
        "--manual-threshold",
        "--mt",
        dest="manual_threshold",
        metavar="DBFS",
        help=(
            f"Fixed trigger threshold in dBFS; unused when auto-threshold is true "
            f"({THRESHOLD_RANGE[0]} .. {THRESHOLD_RANGE[1]}, default: -30.0)."
        ),
    )
    trig_group.add_argument(
        "--pre-trigger-buffers",
        "--ptb",
        dest="pre_trigger_buffers",
        metavar="N",
        help=f"Number of pre-trigger history buffers kept in memory ({PRE_TRIGGER_RANGE[0]} .. {PRE_TRIGGER_RANGE[1]}, default: 2).",
    )
    trig_group.add_argument(
        "--min-burst-seconds",
        "--mbs",
        dest="min_burst_seconds",
        metavar="SEC",
        help=(
            f"Minimum active burst duration in seconds to filter short noise spikes, "
            f"or 0 to disable; supports ms/s suffixes "
            f"({MIN_BURST_RANGE[0]} .. {MIN_BURST_RANGE[1]}, default: 0.0)."
        ),
    )
    trig_group.add_argument(
        "--silence-seconds",
        "--ss",
        dest="silence_seconds",
        metavar="SEC",
        help=(
            f"Continuous silence duration in seconds to stop recording; supports ms/s suffixes "
            f"({SILENCE_RANGE[0]} .. {SILENCE_RANGE[1]}, default: 0.8)."
        ),
    )
    trig_group.add_argument(
        "--max-post-trigger-seconds",
        "--mpts",
        dest="max_post_trigger_seconds",
        metavar="SEC",
        help=(
            f"Maximum recording duration in seconds after trigger; supports ms/s suffixes "
            f"({POST_TRIGGER_RANGE[0]} .. {POST_TRIGGER_RANGE[1]}, default: 4.0)."
        ),
    )
    trig_group.add_argument(
        "--rx-buffer-size",
        "--rbs",
        dest="rx_buffer_size",
        metavar="SAMPLES",
        help=f"DMA receive buffer size in samples ({RX_BUFFER_RANGE[0]} .. {RX_BUFFER_RANGE[1]}, default: 32768).",
    )

    gps_group = parser.add_argument_group("GPS geolocation settings")
    gps_group.add_argument(
        "--enable-gps",
        "--eg",
        dest="enable_gps",
        type=_parse_bool,
        default=None,
        metavar="true|false",
        help="Enable GPS geolocation tagging via gpsd daemon (default: false).",
    )
    gps_group.add_argument(
        "--gpsd-host",
        "--gh",
        dest="gpsd_host",
        metavar="HOST",
        help="gpsd daemon hostname or IP address; unused when enable-gps is false (default: 127.0.0.1).",
    )
    gps_group.add_argument(
        "--gpsd-port",
        "--gp",
        dest="gpsd_port",
        metavar="PORT",
        help="gpsd daemon TCP port; unused when enable-gps is false (1 .. 65535, default: 2947).",
    )

    ui_group = parser.add_argument_group("Interface & system settings")
    ui_group.add_argument(
        "--records-dir",
        "--rd",
        dest="records_dir",
        metavar="PATH",
        help="Directory path for saving and loading SigMF recordings.",
    )
    ui_group.add_argument(
        "--enable-sound",
        "--es",
        dest="enable_sound",
        type=_parse_bool,
        default=None,
        metavar="true|false",
        help="Enable acoustic sound effect cues (default: true).",
    )
    ui_group.add_argument(
        "--enable-mouse",
        "--em",
        dest="enable_mouse",
        type=_parse_bool,
        default=None,
        metavar="true|false",
        help="Enable terminal mouse support in the TUI (default: true).",
    )

    return parser


def _parse_bounded(
    parser: argparse.ArgumentParser,
    flag_name: str,
    raw: str,
    cast_type: type,
    bounds: tuple[float, float | None],
) -> int | float:
    lo, hi = bounds
    try:
        val = parse_number(raw, cast_type, lo, hi)
    except ValueError as err:
        parser.error(f"{flag_name}: {err} (got {raw!r})")
    if val < lo or (hi is not None and val > hi):
        if hi is None:
            parser.error(f"{flag_name}: value {val} must be >= {lo:g}")
        else:
            parser.error(f"{flag_name}: value {val} is out of range [{lo:g} .. {hi:g}]")
    return val


def parse_cli_args(
    argv: Sequence[str] | None = None,
    base_config: AppConfig | None = None,
) -> tuple[CLIOptions, AppConfig]:
    """Parse CLI arguments, validate ranges, and apply overrides to a copy of base_config."""
    parser = build_arg_parser()
    ns = parser.parse_args(argv)

    source_cfg = (
        AppConfig()
        if ns.reset_config
        else (CONFIG if base_config is None else base_config)
    )
    cfg = replace(source_cfg)
    overrides: dict[str, Any] = {}

    if ns.pluto_uri is not None:
        try:
            overrides["pluto_uri"] = normalize_pluto_uri(ns.pluto_uri)
        except ValueError as err:
            parser.error(f"--pluto-uri: {err} (got {ns.pluto_uri!r})")

    if ns.rx_freq is not None:
        overrides["rx_freq"] = int(
            _parse_bounded(parser, "--rx-freq", ns.rx_freq, int, FREQ_RANGE)
        )

    if ns.sample_rate is not None:
        overrides["sample_rate"] = int(
            _parse_bounded(
                parser, "--sample-rate", ns.sample_rate, int, SAMPLE_RATE_RANGE
            )
        )

    if ns.rf_bandwidth is not None:
        try:
            overrides["rf_bandwidth"] = parse_rf_bandwidth(ns.rf_bandwidth)
        except ValueError as err:
            parser.error(f"--rf-bandwidth: {err} (got {ns.rf_bandwidth!r})")

    if ns.rx_gain is not None:
        overrides["rx_gain"] = float(
            _parse_bounded(parser, "--rx-gain", ns.rx_gain, float, RX_GAIN_RANGE)
        )

    if ns.tx_gain is not None:
        overrides["tx_gain"] = float(
            _parse_bounded(parser, "--tx-gain", ns.tx_gain, float, TX_GAIN_RANGE)
        )

    if ns.tx_repeats is not None:
        overrides["tx_repeats"] = int(
            _parse_bounded(
                parser, "--tx-repeats", ns.tx_repeats, int, TX_REPEATS_RANGE
            )
        )

    if ns.auto_capture is not None:
        overrides["auto_capture"] = bool(ns.auto_capture)

    if ns.auto_threshold is not None:
        overrides["auto_threshold"] = bool(ns.auto_threshold)

    if ns.auto_trigger_margin is not None:
        overrides["auto_trigger_margin"] = float(
            _parse_bounded(
                parser,
                "--auto-trigger-margin",
                ns.auto_trigger_margin,
                float,
                AUTO_MARGIN_RANGE,
            )
        )
        if ns.auto_threshold is None and ns.manual_threshold is None:
            overrides["auto_threshold"] = True

    if ns.auto_recal_seconds is not None:
        recal_sec = float(
            _parse_bounded(
                parser,
                "--auto-recal-seconds",
                ns.auto_recal_seconds,
                float,
                AUTO_RECAL_RANGE,
            )
        )
        overrides["auto_recal_seconds"] = recal_sec
        if (
            recal_sec > 0
            and ns.auto_threshold is None
            and ns.manual_threshold is None
        ):
            overrides["auto_threshold"] = True

    if ns.manual_threshold is not None:
        overrides["manual_threshold"] = float(
            _parse_bounded(
                parser,
                "--manual-threshold",
                ns.manual_threshold,
                float,
                THRESHOLD_RANGE,
            )
        )
        if ns.auto_threshold is None:
            overrides["auto_threshold"] = False

    if ns.pre_trigger_buffers is not None:
        overrides["pre_trigger_buffers"] = int(
            _parse_bounded(
                parser,
                "--pre-trigger-buffers",
                ns.pre_trigger_buffers,
                int,
                PRE_TRIGGER_RANGE,
            )
        )

    if ns.min_burst_seconds is not None:
        overrides["min_burst_seconds"] = float(
            _parse_bounded(
                parser,
                "--min-burst-seconds",
                ns.min_burst_seconds,
                float,
                MIN_BURST_RANGE,
            )
        )

    if ns.silence_seconds is not None:
        overrides["silence_seconds"] = float(
            _parse_bounded(
                parser, "--silence-seconds", ns.silence_seconds, float, SILENCE_RANGE
            )
        )

    if ns.max_post_trigger_seconds is not None:
        overrides["max_post_trigger_seconds"] = float(
            _parse_bounded(
                parser,
                "--max-post-trigger-seconds",
                ns.max_post_trigger_seconds,
                float,
                POST_TRIGGER_RANGE,
            )
        )

    if ns.rx_buffer_size is not None:
        overrides["rx_buffer_size"] = int(
            _parse_bounded(
                parser, "--rx-buffer-size", ns.rx_buffer_size, int, RX_BUFFER_RANGE
            )
        )

    if ns.enable_gps is not None:
        overrides["enable_gps"] = bool(ns.enable_gps)

    if ns.gpsd_host is not None:
        host = ns.gpsd_host.strip()
        if not host:
            parser.error("--gpsd-host: host cannot be empty")
        overrides["gpsd_host"] = host

    if ns.gpsd_port is not None:
        overrides["gpsd_port"] = int(
            _parse_bounded(parser, "--gpsd-port", ns.gpsd_port, int, (1, 65535))
        )

    if ns.records_dir is not None:
        rec_dir = ns.records_dir.strip()
        if not rec_dir or "\x00" in rec_dir or not all(ch.isprintable() for ch in rec_dir):
            parser.error("--records-dir: invalid directory path")
        overrides["records_dir"] = os.path.expanduser(rec_dir)

    if ns.enable_sound is not None:
        overrides["enable_sound"] = bool(ns.enable_sound)

    if ns.enable_mouse is not None:
        overrides["enable_mouse"] = bool(ns.enable_mouse)

    mode: str | None = None
    transmit_path: str | None = None
    if ns.capture:
        mode = "capture"
    elif ns.transmit is not None:
        mode = "transmit"
        transmit_path = ns.transmit.strip()
    elif ns.loopback:
        mode = "loopback"

    if ns.headless and mode == "transmit" and not transmit_path:
        parser.error(
            "--transmit in --headless mode requires a file path, recording #, or 'latest'"
        )

    if overrides:
        cfg.update(overrides)
    if cfg.min_burst_seconds > cfg.max_post_trigger_seconds:
        parser.error(
            "--min-burst-seconds: cannot exceed --max-post-trigger-seconds"
        )

    opts = CLIOptions(
        mode=mode,
        transmit_path=transmit_path,
        list_recordings=bool(ns.list_recordings),
        show_config=bool(ns.show_config),
        headless=bool(ns.headless),
        save_config=bool(ns.save_config),
        reset_config=bool(ns.reset_config),
        config_overrides=overrides,
    )
    return opts, cfg


def resolve_transmit_target(target: str, records_dir: str) -> str:
    """Resolve a CLI transmit target ('latest', 1-based index, filename, or path) to a .sigmf-data path."""
    cleaned = os.path.expanduser(target.strip())
    if cleaned.lower() == "latest":
        files = sorted(
            glob.glob(os.path.join(records_dir, "*.sigmf-data")), reverse=True
        )
        if not files:
            raise FileNotFoundError(f"No recordings found in {records_dir}")
        return files[0]

    if cleaned.isdigit():
        files = sorted(
            glob.glob(os.path.join(records_dir, "*.sigmf-data")), reverse=True
        )
        if not files:
            raise FileNotFoundError(f"No recordings found in {records_dir}")
        idx = int(cleaned)
        if not (1 <= idx <= len(files)):
            raise FileNotFoundError(
                f"Recording #{idx} out of range (1..{len(files)} available in {records_dir})"
            )
        return files[idx - 1]

    candidates = [cleaned]
    if not cleaned.endswith(".sigmf-data"):
        candidates.append(f"{cleaned}.sigmf-data")
    if not os.path.isabs(cleaned):
        candidates.append(os.path.join(records_dir, cleaned))
        if not cleaned.endswith(".sigmf-data"):
            candidates.append(os.path.join(records_dir, f"{cleaned}.sigmf-data"))

    for cand in candidates:
        if os.path.isfile(cand):
            return cand

    return cleaned


def list_recordings(config: AppConfig | None = None) -> int:
    """Print available SigMF recordings in records_dir with index numbers and metadata."""
    cfg = CONFIG if config is None else config
    color = _supports_color(sys.stdout)
    records_dir = cfg.records_dir
    files = sorted(
        glob.glob(os.path.join(records_dir, "*.sigmf-data")), reverse=True
    )
    if not files:
        print(_ansi(f"No recordings found in {records_dir}", _ANSI_WARN, color=color))
        return 0

    print(_ansi(f"Recordings in {records_dir} ({len(files)} found):", _ANSI_HEADING, color=color))
    rows: list[tuple[str, str, str, str, str, str, bool]] = []
    for idx, f in enumerate(files, start=1):
        fname = os.path.basename(f)
        try:
            use_sr, use_freq, err = load_sigmf_meta(f)
            if err is not None or use_sr is None or use_freq is None:
                rows.append((f"[{idx}]", fname, f"<invalid: {err or 'meta'}>", "", "", "", True))
                continue
            n_samples = os.path.getsize(f) // BYTES_PER_SAMPLE
            duration = n_samples / use_sr if use_sr > 0 else 0.0
            freq_str = f"{use_freq / 1e6:.3f} MHz"
            sr_str = f"{use_sr / 1e6:.2f} MSPS"
            samples_str = f"{n_samples:,} samples ({duration:.2f} s)"
            geo = load_sigmf_geolocation(f)
            if geo is not None:
                lon, lat, _alt = geo
                lat_card = "N" if lat >= 0 else "S"
                lon_card = "E" if lon >= 0 else "W"
                geo_str = f"[{abs(lat):.3f}°{lat_card}, {abs(lon):.3f}°{lon_card}]"
            else:
                geo_str = ""
            rows.append((f"[{idx}]", fname, freq_str, sr_str, samples_str, geo_str, False))
        except OSError:
            rows.append((f"[{idx}]", fname, "<unreadable>", "", "", "", True))

    w_idx = max(len(r[0]) for r in rows)
    w_name = max(len(r[1]) for r in rows)
    w_freq = max(len(r[2]) for r in rows)
    w_sr = max(len(r[3]) for r in rows)
    w_samp = max(len(r[4]) for r in rows)

    for idx_s, fname, freq_s, sr_s, samp_s, geo_s, is_err in rows:
        idx_cell = _ansi(f"{idx_s:>{w_idx}}", _ANSI_CYAN, color=color)
        freq_cell = (
            _ansi(f"{freq_s:>{w_freq}}", _ANSI_ERR, color=color)
            if is_err
            else f"{freq_s:>{w_freq}}"
        )
        line = f"  {idx_cell}  {fname:<{w_name}}  {freq_cell}  {sr_s:>{w_sr}}  {samp_s:<{w_samp}}"
        if geo_s:
            line += f"  {_ansi(geo_s, _ANSI_OK, color=color)}"
        print(line.rstrip())
    return 0


def show_config(config: AppConfig | None = None) -> int:
    """Print the active configuration grouped by section with CLI flags and units."""
    cfg = CONFIG if config is None else config
    color = _supports_color(sys.stdout)
    cfg_path = config_mod.CONFIG_FILE
    on_disk = os.path.isfile(cfg_path)
    status = "saved on disk" if on_disk else "defaults, file not created"
    status_colored = _ansi(status, _ANSI_OK if on_disk else _ANSI_WARN, color=color)
    header = _ansi(f"Configuration ({cfg_path} - ", _ANSI_HEADING, color=color)
    suffix = _ansi("):", _ANSI_HEADING, color=color)
    print(f"{header}{status_colored}{suffix}")

    rf_bw_str = (
        f"0  (auto: {resolve_rf_bandwidth(cfg.sample_rate, 0) / 1e6:.2f} MHz)"
        if cfg.rf_bandwidth == 0
        else f"{cfg.rf_bandwidth}  ({cfg.rf_bandwidth / 1e6:.2f} MHz)"
    )
    min_burst_str = (
        "0.000  (off)"
        if cfg.min_burst_seconds == 0
        else f"{cfg.min_burst_seconds:.3f}  ({cfg.min_burst_seconds * 1000:.0f} ms)"
    )
    auto_recal_str = (
        f"{cfg.auto_recal_seconds:.1f}  (unused: auto off)"
        if not cfg.auto_threshold
        else (
            "0.0  (off)"
            if cfg.auto_recal_seconds == 0
            else f"{cfg.auto_recal_seconds:.1f}"
        )
    )

    sections: list[tuple[str, list[tuple[str, str, str]]]] = [
        (
            "Radio & Connection",
            [
                ("Pluto URI", "--pluto-uri, --pu", cfg.pluto_uri),
                (
                    "RX freq (Hz)",
                    "--rx-freq, --rf",
                    f"{cfg.rx_freq}  ({cfg.rx_freq / 1e6:.3f} MHz)",
                ),
                (
                    "Sample rate (Hz)",
                    "--sample-rate, --sr",
                    f"{cfg.sample_rate}  ({cfg.sample_rate / 1e6:.2f} MSPS)",
                ),
                ("RF bandwidth (Hz)", "--rf-bandwidth, --rb", rf_bw_str),
                ("RX gain (dB)", "--rx-gain, --rg", f"{cfg.rx_gain:.2f}"),
                ("TX gain (dB)", "--tx-gain, --tg", f"{cfg.tx_gain:.2f}"),
                ("TX repeats", "--tx-repeats, --tr", str(cfg.tx_repeats)),
            ],
        ),
        (
            "Capture Engine & Trigger",
            [
                (
                    "Auto capture",
                    "--auto-capture, --ac",
                    "true" if cfg.auto_capture else "false",
                ),
                (
                    "Auto threshold",
                    "--auto-threshold, --at",
                    "true" if cfg.auto_threshold else "false",
                ),
                (
                    "Auto trigger margin (dB)",
                    "--auto-trigger-margin, --atm",
                    f"{cfg.auto_trigger_margin:.1f}"
                    + ("" if cfg.auto_threshold else "  (unused: auto off)"),
                ),
                (
                    "Auto recalibrate seconds",
                    "--auto-recal-seconds, --ars",
                    auto_recal_str,
                ),
                (
                    "Manual threshold (dBFS)",
                    "--manual-threshold, --mt",
                    f"{cfg.manual_threshold:.1f}"
                    + ("" if not cfg.auto_threshold else "  (unused: auto on)"),
                ),
                (
                    "Pre-trigger buffers",
                    "--pre-trigger-buffers, --ptb",
                    str(cfg.pre_trigger_buffers),
                ),
                ("Min burst seconds", "--min-burst-seconds, --mbs", min_burst_str),
                (
                    "Silence seconds",
                    "--silence-seconds, --ss",
                    f"{cfg.silence_seconds:.2f}",
                ),
                (
                    "Max post-trigger seconds",
                    "--max-post-trigger-seconds, --mpts",
                    f"{cfg.max_post_trigger_seconds:.2f}",
                ),
                (
                    "RX buffer size",
                    "--rx-buffer-size, --rbs",
                    str(cfg.rx_buffer_size),
                ),
            ],
        ),
        (
            "GPS Geolocation",
            [
                (
                    "Enable GPS (gpsd)",
                    "--enable-gps, --eg",
                    "true" if cfg.enable_gps else "false",
                ),
                (
                    "gpsd host",
                    "--gpsd-host, --gh",
                    cfg.gpsd_host + ("" if cfg.enable_gps else "  (unused: GPS off)"),
                ),
                (
                    "gpsd port",
                    "--gpsd-port, --gp",
                    str(cfg.gpsd_port)
                    + ("" if cfg.enable_gps else "  (unused: GPS off)"),
                ),
            ],
        ),
        (
            "Interface & System",
            [
                ("Records dir", "--records-dir, --rd", cfg.records_dir),
                (
                    "Enable sound",
                    "--enable-sound, --es",
                    "true" if cfg.enable_sound else "false",
                ),
                (
                    "Enable mouse",
                    "--enable-mouse, --em",
                    "true" if cfg.enable_mouse else "false",
                ),
            ],
        ),
    ]

    all_rows = [row for _, rows in sections for row in rows]
    w_label = max(len(r[0]) for r in all_rows)
    w_flag = max(len(r[1]) for r in all_rows)

    for section_title, rows in sections:
        print(f"\n  {_ansi(f'[{section_title}]', _ANSI_HEADING, color=color)}")
        for label, flag, value in rows:
            flag_cell = _ansi(f"{flag:<{w_flag}}", _ANSI_CYAN, color=color)
            if "(unused:" in value or "(off)" in value:
                val_cell = _ansi(value, _ANSI_WARN, color=color)
            else:
                val_cell = value
            print(f"    {label:<{w_label}}  {flag_cell}  {val_cell}")
    return 0


_LEVEL_CODES = {
    "info": "",
    "accent": _ANSI_CYAN,
    "ok": _ANSI_OK,
    "warn": _ANSI_WARN,
    "err": _ANSI_ERR,
}


def _clear_inline_status() -> None:
    """Erase the transient inline signal meter line before printing a log event."""
    global _INLINE_STATUS_ACTIVE
    if _INLINE_STATUS_ACTIVE:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        _INLINE_STATUS_ACTIVE = False


def _draw_headless_meter(
    lvl: float,
    trigger_threshold: float,
    saturated: bool,
    recalibrating: bool = False,
) -> None:
    """Render an in-place single-line signal bar matching the TUI Signal widget."""
    global _INLINE_STATUS_ACTIVE
    if not _is_tty(sys.stdout):
        return
    try:
        level = float(lvl)
        threshold = float(trigger_threshold)
    except (ValueError, TypeError):
        return
    if not math.isfinite(level) or not math.isfinite(threshold):
        return

    cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    meter_w = max(1, min(40, cols - 46))
    bars = max(
        0,
        min(
            meter_w,
            int((level - METER_FLOOR_DBFS) / -METER_FLOOR_DBFS * meter_w),
        ),
    )
    color = _supports_color(sys.stdout)
    filled = _ansi("#" * bars, _ANSI_OK, color=color)
    empty = _ansi("." * (meter_w - bars), _ANSI_DIM, color=color)
    info = f" {level:6.1f} dBFS   thr {threshold:.0f}"
    recal_part = f" {_ansi('(recal)', _ANSI_CYAN, color=color)}" if recalibrating else ""
    sat = f" {_ansi('SAT!', _ANSI_ERR_BOLD, color=color)}" if saturated else ""
    sys.stdout.write(f"\r\033[KSignal: [{filled}{empty}]{info}{recal_part}{sat}")
    sys.stdout.flush()
    _INLINE_STATUS_ACTIVE = True


def _log(msg: str, *, level: str | None = None, err: bool = False) -> None:
    _clear_inline_status()
    stream = sys.stderr if err else sys.stdout
    color = _supports_color(stream)
    resolved = ("err" if err else "info") if level is None else level
    code = _LEVEL_CODES.get(resolved, "")
    ts = _ansi(f"[{time.strftime('%H:%M:%S')}]", _ANSI_DIM, color=color)
    body = _ansi(msg, code, color=color) if code else msg
    print(f"{ts} {body}", file=stream, flush=True)


def run_headless(opts: CLIOptions, config: AppConfig | None = None) -> int:
    """Execute PlutoSiege in console/headless mode without curses."""
    cfg = CONFIG if config is None else config
    if opts.show_config:
        show_config(cfg)
    if opts.list_recordings:
        list_recordings(cfg)
    if opts.show_config or opts.list_recordings:
        return 0
    mode = opts.mode
    if mode is None:
        if opts.save_config or opts.reset_config:
            return 0
        mode = "capture"

    sdr = None
    hw_model = "PlutoSDR"
    try:
        _log(f"Connecting to PlutoSDR at {cfg.pluto_uri} ...")
        try:
            sdr, hw_model = open_sdr(cfg.pluto_uri, hw_model)
        except Exception as e:
            _log(f"Cannot connect to {cfg.pluto_uri}: {e}", level="err", err=True)
            return 1

        _log(f"Connected: PlutoSDR ({cfg.pluto_uri})", level="ok")
        init_gps(cfg)

        if mode == "capture":
            return _headless_capture(sdr, hw_model, cfg, continuous=cfg.auto_capture)
        if mode == "transmit":
            return _headless_transmit(sdr, opts.transmit_path or "", cfg)
        if mode == "loopback":
            return _headless_loopback(sdr, cfg)

        _log(f"Unknown mode: {mode}", level="err", err=True)
        return 1
    except KeyboardInterrupt:
        _log("Interrupted by user (Ctrl+C). Stopping...", level="warn")
        return 0
    finally:
        release_sdr(sdr)
        stop_gps()


def _headless_capture(
    sdr: Any, hw_model: str, cfg: AppConfig, continuous: bool
) -> int:
    os.makedirs(cfg.records_dir, exist_ok=True)
    _log("Configuring receiver...")

    saved_count = 0
    try:
        engine = CaptureEngine(sdr, config=cfg)
        engine.prepare()

        if not engine.io_timeout_available:
            _log(f"Warning: {IO_TIMEOUT_UNAVAILABLE}", level="warn", err=True)

        if engine.actual_sr > 4_000_000:
            _log("Warning: SR > 4 MSPS. USB 2.0 may drop samples.", level="warn", err=True)

        rf_line, trigger_line = format_capture_summary(
            engine.actual_freq,
            engine.actual_sr,
            engine.trigger_threshold,
            engine.noise_floor,
            cfg,
        )
        _log(rf_line)
        _log(trigger_line)
        if cfg.enable_gps:
            mgr = get_gps_manager()
            gps_status = mgr.get_status_text()
            gps_tag, gps_level = classify_gps_status(
                gps_status, has_fix=mgr.get_latest_fix().has_fix
            )
            _log(f"GPS: {gps_tag}{gps_status}", level=gps_level)
        if engine.trigger_threshold >= 0.0:
            _log("Warning: threshold >= 0 dBFS, nothing can trigger.", level="warn", err=True)
        play_cue(SoundCue.START, enabled=cfg.enable_sound)

        last_draw = [0.0]
        recal_until = [0.0]

        def on_trigger_detected() -> None:
            play_cue(SoundCue.TRIGGER, enabled=cfg.enable_sound)
            _log(
                f"Signal detected ({engine.current_level_dbfs:.1f} dBFS)! Recording...",
                level="accent",
            )

        def on_meter_update(lvl: float, saturated: bool) -> None:
            now = time.monotonic()
            if now - last_draw[0] > METER_REFRESH_SECONDS:
                _draw_headless_meter(
                    lvl,
                    engine.trigger_threshold,
                    saturated,
                    recalibrating=now < recal_until[0],
                )
                last_draw[0] = now

        def on_recalibrated(_nf: float, thr: float) -> None:
            now = time.monotonic()
            recal_until[0] = now + RECAL_INDICATOR_SECONDS
            _draw_headless_meter(
                engine.current_level_dbfs,
                thr,
                engine.current_saturated,
                recalibrating=True,
            )
            last_draw[0] = now

        while True:
            if continuous:
                _log(
                    f"[Saved: {saved_count}] Listening for signal "
                    "(press Ctrl+C to stop)..."
                )
            else:
                _log("Listening for signal (press Ctrl+C to abort)...")

            _draw_headless_meter(
                engine.current_level_dbfs if saved_count > 0 else -100.0,
                engine.trigger_threshold,
                engine.current_saturated,
            )
            engine.listen_and_capture(
                check_abort=lambda: False,
                on_trigger_detected=on_trigger_detected,
                on_meter_update=on_meter_update,
                on_recalibrated=on_recalibrated,
            )

            if engine.captured_data is None or engine.start_time is None:
                _log("No signal captured.", level="warn", err=True)
                if not continuous:
                    return 1
                continue

            prefix = f"[#{saved_count + 1}] " if continuous else ""
            if engine.is_partial:
                _log(f"{prefix}Partial recording (max post-trigger limit reached).", level="warn")
            if engine.current_saturated:
                _log(f"{prefix}SATURATION detected. Lower RX gain.", level="warn")

            gps_fix = (
                get_gps_manager().get_latest_fix() if cfg.enable_gps else None
            )
            base = engine.save_recording(
                cfg.records_dir, hw_model, gps_fix=gps_fix
            )
            saved_count += 1
            if gps_fix is not None and gps_fix.has_fix:
                lat_card = "N" if gps_fix.latitude >= 0 else "S"
                lon_card = "E" if gps_fix.longitude >= 0 else "W"
                _log(
                    f"{prefix}Geotag: {abs(gps_fix.latitude):.5f}°{lat_card}, "
                    f"{abs(gps_fix.longitude):.5f}°{lon_card} ({gps_fix.satellites} sats)"
                )
            total_samples = int(engine.captured_data.size)
            duration = total_samples / engine.actual_sr if engine.actual_sr > 0 else 0.0
            play_cue(SoundCue.SUCCESS, enabled=cfg.enable_sound)
            _log(
                f"{prefix}Saved {os.path.basename(base)}.sigmf-data "
                f"({total_samples:,} samples, {duration:.2f} s)",
                level="ok",
            )

            if not continuous:
                return 0
    except KeyboardInterrupt:
        if continuous:
            _log(
                f"Stopped. Total recordings saved: {saved_count}.",
                level="ok" if saved_count > 0 else "warn",
            )
        else:
            _log("Capture aborted.", level="warn")
        return 0
    except (OSError, RuntimeError, ValueError) as e:
        play_cue(SoundCue.FAIL, enabled=cfg.enable_sound)
        _log(f"Capture failed: {e}", level="err", err=True)
        return 1
    finally:
        _clear_inline_status()
        cleanup_sdr(sdr)


def _headless_transmit(sdr: Any, target: str, cfg: AppConfig) -> int:
    try:
        path = resolve_transmit_target(target, cfg.records_dir)
        data, use_sr, use_freq = TransmitEngine.prepare_transmission(path)
        rf_line, file_line = format_tx_summary(
            use_freq, use_sr, int(data.size), path, cfg
        )
        _log(rf_line)
        _log(file_line)
        geo = load_sigmf_geolocation(path)
        if geo is not None:
            lon, lat, alt = geo
            lat_card = "N" if lat >= 0 else "S"
            lon_card = "E" if lon >= 0 else "W"
            _log(
                f"Geotag: {abs(lat):.5f}°{lat_card}, "
                f"{abs(lon):.5f}°{lon_card} ({alt:.1f} m)"
            )

        _log("Transmitting...", level="accent")
        play_cue(SoundCue.TX_START, enabled=cfg.enable_sound)
        timeout_ok = TransmitEngine.transmit(sdr, data, use_sr, use_freq, config=cfg)
        if not timeout_ok:
            _log(f"Warning: {IO_TIMEOUT_UNAVAILABLE}", level="warn", err=True)
        play_cue(SoundCue.TX_COMPLETE, enabled=cfg.enable_sound)
        _log("Signal replayed successfully!", level="ok")
        return 0
    except (OSError, RuntimeError, ValueError) as e:
        play_cue(SoundCue.FAIL, enabled=cfg.enable_sound)
        _log(f"TX failed: {e}", level="err", err=True)
        return 1


def _headless_loopback(sdr: Any, cfg: AppConfig) -> int:
    try:
        _log("Running digital loopback test...")
        _log(format_loopback_summary(cfg))
        play_cue(SoundCue.START, enabled=cfg.enable_sound)
        snr_db, tone_freq, timeout_ok = LoopbackTester.run_test(
            sdr, cfg.sample_rate, cfg.rx_buffer_size, config=cfg
        )
        if not timeout_ok:
            _log(f"Warning: {IO_TIMEOUT_UNAVAILABLE}", level="warn", err=True)
        _log(f"Test tone: {tone_freq / 1000:.3f} kHz")
        if snr_db >= LOOPBACK_MIN_SNR_DB:
            play_cue(SoundCue.PASS, enabled=cfg.enable_sound)
            _log(f"PASSED. SNR: {snr_db:.1f} dB", level="ok")
            return 0
        play_cue(SoundCue.FAIL, enabled=cfg.enable_sound)
        _log(f"FAILED. SNR: {snr_db:.1f} dB", level="err", err=True)
        return 1
    except Exception as e:
        play_cue(SoundCue.FAIL, enabled=cfg.enable_sound)
        _log(f"Loopback failed: {e}", level="err", err=True)
        return 1


def apply_cli_to_global_config(opts: CLIOptions, parsed_cfg: AppConfig) -> AppConfig:
    """Apply parsed CLI configuration to global CONFIG and optionally save to disk."""
    CONFIG.update(parsed_cfg.to_dict())
    if opts.reset_config or opts.save_config:
        if save_settings(CONFIG):
            if opts.reset_config and not opts.config_overrides:
                _log("Defaults restored and saved to settings.json.", level="ok")
            elif opts.reset_config:
                _log(
                    "Defaults restored (with CLI overrides) and saved to settings.json.",
                    level="ok",
                )
            else:
                _log("Configuration saved to settings.json.", level="ok")
        else:
            _log("Failed to save configuration to settings.json.", level="err", err=True)
    return CONFIG


def sig_handler(signum: int, frame: Any) -> None:
    """Defer cleanup to normal control flow; signal handlers must not lock."""
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> None:
    """Primary CLI and TUI entry point for PlutoSiege."""
    opts, parsed_cfg = parse_cli_args(argv)
    cfg = apply_cli_to_global_config(opts, parsed_cfg)

    if opts.show_config:
        show_config(cfg)
    if opts.list_recordings:
        list_recordings(cfg)
    if opts.show_config or opts.list_recordings:
        return
    if (opts.reset_config or opts.save_config) and opts.mode is None and not opts.headless:
        return

    for sig_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, sig_handler)
            except (ValueError, OSError):
                pass

    if opts.headless:
        code = run_headless(opts, config=cfg)
        if code != 0:
            sys.exit(code)
        return

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            "Requires an interactive terminal (or pass --headless for text mode).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        import curses

        from pluto_siege.ui.screens.main_menu import _cleanup_active_sdr, curses_app
    except ImportError as e:
        print(
            f"Curses TUI is not available ({e}). Pass --headless for console mode.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        curses.wrapper(lambda win: curses_app(win, config=cfg, cli_opts=opts))
    except KeyboardInterrupt:
        pass
    except curses.error as e:
        print(f"Terminal error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        _cleanup_active_sdr()
    print("Goodbye!")
