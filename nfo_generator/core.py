"""Core helpers, constants, config, libmediainfo discovery, formatting.

This is the foundational module of the ``nfo_generator`` package — every
other submodule imports from it. Keeping all "infrastructure" code together
here avoids the cycle issues that come up when constants, helpers, and
formatting are split across files (sections 1 through 8 of the original
single-file layout).
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from fractions import Fraction
from math import gcd
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from langcodes import Language
from pymediainfo import MediaInfo



# =============================================================================
# 1. CONSTANTS
# =============================================================================

# -- Display aspect ratios ----------------------------------------------------
# Common DARs found in video releases. When a computed ratio is close enough
# to one of these (see CLOSEST_RATIO_TOLERANCE), we snap to the canonical
# label instead of printing an ugly fraction like "172:93".
STANDARD_RATIOS: dict[str, float] = {
    "4:3":     4 / 3,    # 1.333 — analog SDTV
    "16:10":   16 / 10,  # 1.600 — older computer monitors
    "5:3":     5 / 3,    # 1.667 — Super 16
    "1.66:1":  1.66,     # European films
    "16:9":    16 / 9,   # 1.778 — HDTV / streaming standard
    "1.85:1":  1.85,     # US cinema "flat"
    "2.00:1":  2.00,     # Univisium
    "2.20:1":  2.20,     # Classic 70 mm
    "2.35:1":  2.35,     # CinemaScope
    "2.39:1":  2.39,     # Modern anamorphic
    "2.40:1":  2.40,     # 2.39 rounded
    "2.76:1":  2.76,     # Ultra Panavision 70
}
CLOSEST_RATIO_TOLERANCE: float = 0.015  # ±1.5 % is "close enough" to a standard

# -- Sources offered by the CLI / GUI -----------------------------------------
DEFAULT_SOURCES: list[str] = [
    "Netflix (NF)",
    "Prime Video (AMZN)",
    "Crunchyroll (CR)",
    "Disney+ (DSNP)",
    "Apple TV+ (ATVP)",
    "Max (HMAX)",
    "ADN",
    "BluRay",
    "UHD BluRay",
    "WEB-DL",
    "Other",
]

# When the source implies a specific delivery type, this maps to the suffix
# appended to the video codec label, e.g. "x265 - BluRay" or "x264 - WEB-DL".
SOURCE_DELIVERY_SUFFIX: dict[str, str] = {
    "BluRay":     "BluRay",
    "UHD BluRay": "UHD BluRay",
}
# Default suffix for streaming-style sources
DEFAULT_DELIVERY_SUFFIX: str = "WEB-DL"

# -- File extensions accepted by the picker / drag & drop ---------------------
VIDEO_EXTENSIONS: set[str] = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".webm", ".m2ts",
}

# -- Audio channel layout normalization ---------------------------------------
# MediaInfo reports a raw channel count; we map it to a release-standard label.
AUDIO_CHANNELS_MAP: dict[str, str] = {
    "1": "Mono",
    "2": "2.0",
    "3": "2.1",
    "6": "5.1",
    "7": "6.1",
    "8": "7.1",
}

# Audio codec commercial-name normalization (shortened release-style labels).
AUDIO_CODEC_MAP: dict[str, str] = {
    "DTS-HD Master Audio":                  "DTS-HD MA",
    "DTS-HD High Resolution Audio":         "DTS-HD HRA",
    "DTS-ES":                               "DTS-ES",
    "Dolby Digital Plus with Dolby Atmos":  "E-AC-3 JOC",
    "Dolby Digital Plus":                   "E-AC-3",
    "Dolby Digital":                        "AC-3",
    "Dolby TrueHD with Dolby Atmos":        "TrueHD Atmos",
}

# -- libmediainfo search paths ------------------------------------------------
# Cross-platform: the project-local ./lib/ folder is checked first (the
# preferred, fully portable setup — PyInstaller bundles the right binary per
# OS there), covering macOS (.dylib), Linux (.so) and Windows (.dll). The
# remaining entries are system fallbacks so the script still works if the
# user hasn't bundled the library yet.
PROJECT_DIR: Path = Path(__file__).resolve().parent
MEDIAINFO_LIB_CANDIDATES: list[Path] = [
    PROJECT_DIR / "lib" / "libmediainfo.0.dylib",
    PROJECT_DIR / "lib" / "libmediainfo.dylib",
    PROJECT_DIR / "lib" / "libmediainfo.so.0",
    PROJECT_DIR / "lib" / "libmediainfo.so",
    PROJECT_DIR / "lib" / "MediaInfo.dll",
    Path("/Applications/MediaInfo.app/Contents/MacOS/libmediainfo.0.dylib"),
    Path("/Applications/MediaInfo.app/Contents/MacOS/libmediainfo.dylib"),
    Path("/Applications/MediaInfo.app/Contents/Frameworks/libmediainfo.0.dylib"),
    Path("/opt/homebrew/lib/libmediainfo.0.dylib"),
    Path("/opt/homebrew/lib/libmediainfo.dylib"),
    Path("/usr/local/lib/libmediainfo.0.dylib"),
    Path("/usr/local/lib/libmediainfo.dylib"),
    Path("/usr/lib/x86_64-linux-gnu/libmediainfo.so.0"),
    Path("/usr/lib/libmediainfo.so.0"),
]


# =============================================================================
# 1b. USER CONFIG PERSISTENCE
# =============================================================================
# The GUI remembers the user's last choices (source, banner text/font,
# default links, extraction settings…) between runs so they don't have to
# re-enter everything for each release. Stored as JSON in the user's home
# directory. Failure to read/write the config never aborts the program.

CONFIG_PATH: Path = Path.home() / ".nfo_generator.json"
LOG_PATH: Path = Path.home() / ".nfo_generator.log"


# =============================================================================
# Logging — best-effort file log at ~/.nfo_generator.log
# =============================================================================
# Used to record errors that we would otherwise swallow (failed ffmpeg
# extractions, TMDB hiccups, malformed config). Rotates at 1 MB, keeps two
# backups. If the log file can't be opened (read-only $HOME, etc.), logging
# is silently disabled — never a reason for the app to fail to start.

def _setup_logger() -> logging.Logger:
    log = logging.getLogger("nfo_generator")
    if log.handlers:  # already configured (e.g. on hot reload)
        return log
    log.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        log.addHandler(handler)
    except OSError:
        # No writable log file → keep going without one.
        pass
    return log


logger = _setup_logger()

# The schema + defaults for the persisted config. `load_user_config` always
# returns a dict containing exactly these keys (missing keys are backfilled).
DEFAULT_USER_CONFIG: dict = {
    "source":          "Netflix (NF)",
    "banner_font":     "block",
    "header_text":     "",
    "footer_text":     "",
    "links":           {},      # {label: url}
    "sample_duration": 60,
    "screens_count":   5,
    "tmdb_api_key":    "",      # stored locally only, never committed
    "tmdb_language":   "en-US",
    "tvdb_api_key":    "",      # ditto for TVDB v4 API
    "tvdb_pin":        "",      # optional subscriber PIN
    "provider":        "TMDB",
    "font_family":     "",      # GUI interface font (empty = use theme default)
    "appearance_mode": "Dark",  # "Light" | "Dark" | "System"
    "check_updates_on_startup": True,
    "include_prereleases":      False,
    "github_repo":     "Sonje03/nfo-generator",
}


def load_user_config() -> dict:
    """Return the persisted config, backfilled with defaults. Never raises."""
    config = dict(DEFAULT_USER_CONFIG)
    try:
        if CONFIG_PATH.is_file():
            with CONFIG_PATH.open(encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                config.update({k: stored[k] for k in stored if k in config})
    except (OSError, json.JSONDecodeError):
        pass  # fall back to defaults silently
    return config


def save_user_config(config: dict) -> None:
    """Persist the given config to disk. Never raises (best-effort)."""
    try:
        # Only persist known keys to keep the file tidy.
        clean = {k: config[k] for k in DEFAULT_USER_CONFIG if k in config}
        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
    except OSError:
        pass


# =============================================================================
# 2. libmediainfo DISCOVERY
# =============================================================================

class LibMediaInfoNotFound(RuntimeError):
    """Raised when libmediainfo cannot be located on the system."""


def humanize_network_error(exc: Exception, provider: str) -> str:
    """
    Turn a raw ``urllib.error.URLError`` / ``OSError`` into a short,
    actionable message suitable for showing in the GUI's status bar.

    The default urllib representation surfaces socket-level details
    (``[Errno 8] nodename nor servname provided, or not known``) that are
    correct but unhelpful for end users. This helper maps the most
    frequent failure modes to one-line hints.
    """
    text = str(exc).lower()
    # gaierror / DNS lookup failures — extremely common on flaky or
    # captive-portal networks, and what the user saw when the screenshots
    # were taken.
    if any(s in text for s in (
        "nodename nor servname",
        "name or service not known",
        "temporary failure in name resolution",
        "no address associated with hostname",
    )):
        return f"{provider}: Network unreachable — check your internet connection."
    if "connection refused" in text:
        return f"{provider}: Connection refused — firewall, VPN or local proxy?"
    if "timed out" in text or "timeout" in text:
        return f"{provider}: Connection timed out — slow or blocked network."
    if "ssl" in text or "certificate" in text:
        return f"{provider}: SSL/TLS error — check your system clock and CA certificates."
    if "network is unreachable" in text:
        return f"{provider}: Network unreachable — are you offline?"
    # Fallback: short technical message, no full traceback noise.
    short = str(exc)
    if len(short) > 120:
        short = short[:120] + "…"
    return f"{provider}: Network error — {short}"


def find_libmediainfo() -> Optional[str]:
    """Return the first existing path in MEDIAINFO_LIB_CANDIDATES, or None."""
    for candidate in MEDIAINFO_LIB_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


def parse_media(file_path: str) -> MediaInfo:
    """
    Parse a media file via pymediainfo, auto-resolving libmediainfo.

    Raises
    ------
    LibMediaInfoNotFound
        If the shared library cannot be loaded.
    """
    lib_path = find_libmediainfo()
    try:
        if lib_path:
            return MediaInfo.parse(file_path, library_file=lib_path)
        # Last-chance fallback: let pymediainfo try its own search.
        return MediaInfo.parse(file_path)
    except OSError as exc:
        raise LibMediaInfoNotFound(
            "libmediainfo could not be loaded.\n\n"
            "To make the project self-contained, copy the library into:\n"
            f"  {PROJECT_DIR / 'lib'}/\n\n"
            "On macOS with the MediaInfo GUI app installed, you can run:\n"
            "  mkdir -p lib && \\\n"
            "  cp /Applications/MediaInfo.app/Contents/MacOS/libmediainfo*.dylib lib/"
        ) from exc


def get_raw_mediainfo_text(file_path: str) -> Optional[str]:
    """
    Return MediaInfo's full human-readable text dump for `file_path`
    (the same listing the MediaInfo GUI shows), or None on failure.

    The absolute directory is stripped from the "Complete name" line so the
    output shows only the filename — both for privacy (no home path leaks
    into a published .nfo) and to match the conventional scene layout.
    """
    lib_path = find_libmediainfo()
    # output="" → MediaInfo's default text format; full=False → the concise
    # one-value-per-field listing (the GUI default). Without full=False,
    # pymediainfo passes --Full and every field is repeated 5-6 times.
    kwargs: dict = {"output": "", "full": False}
    if lib_path:
        kwargs["library_file"] = lib_path
    try:
        result = MediaInfo.parse(file_path, **kwargs)
    except Exception:  # noqa: BLE001 — best-effort; never break .nfo generation
        return None
    if not isinstance(result, str):
        return None
    # Replace the absolute directory with nothing so only the basename remains.
    directory = os.path.dirname(os.path.abspath(file_path))
    if directory:
        result = result.replace(directory + os.sep, "").replace(directory, "")
    return result.strip("\n")


def get_raw_mediainfo_for_path(path: str) -> Optional[str]:
    """
    Raw MediaInfo dump for a file or a folder.

    - File   → its own dump.
    - Folder → the dump of the first episode, prefixed with a
      "FOR REFERENCE — <filename>" header, since MediaInfo can't read a
      whole folder and a season's episodes share the same technical specs.
    """
    if os.path.isdir(path):
        videos = list_video_files(path)
        if not videos:
            return None
        raw = get_raw_mediainfo_text(videos[0])
        if not raw:
            return None
        return f"FOR REFERENCE — {os.path.basename(videos[0])}\n\n{raw}"
    return get_raw_mediainfo_text(path)


# =============================================================================
# 3. TRACK FIELD HELPERS
# =============================================================================

def field(track: Any, *names: str, default: Any = None) -> Any:
    """
    Return the first non-empty value found on `track` for the given names.

    pymediainfo exposes MediaInfo's fields both as Python attributes
    (lowercased + underscored: `track.bit_rate`) and through `track.to_data()`.
    This helper tries every spelling, then the data dict, before giving up.
    """
    if track is None:
        return default
    data = track.to_data() if hasattr(track, "to_data") else {}
    for name in names:
        value = getattr(track, name, None)
        if value not in (None, ""):
            return value
        value = data.get(name)
        if value not in (None, ""):
            return value
    return default


def field_list(track: Any, *names: str) -> list[str]:
    """
    Variant of `field()` that returns a list of values (one per `/`-separated
    chunk in the underlying MediaInfo string). Useful for fields where a
    single string holds parallel values for multiple HDR formats, languages,
    etc. — for example `HDR_Format = "Dolby Vision / SMPTE ST 2094 App 4"`.
    """
    raw = field(track, *names, default="") or ""
    return [chunk.strip() for chunk in str(raw).split("/")]


# =============================================================================
# 4. NUMERIC / TEXT FORMATTING HELPERS
# =============================================================================

def format_size(size_bytes: Any) -> str:
    """Convert a byte count to a human-readable string (KiB / MiB / GiB / …)."""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    try:
        size = float(size_bytes)
    except (TypeError, ValueError):
        return ""
    for unit in units:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} {units[-1]}"


def format_duration(duration_ms: Any) -> str:
    """
    Convert a millisecond duration to "<h> h <m> m <s> s" (omitting hours
    when zero). pymediainfo reports `track.duration` in milliseconds.
    """
    try:
        total_seconds = int(float(duration_ms) / 1000)
    except (TypeError, ValueError):
        return ""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours} h {minutes} m {seconds} s"
    return f"{minutes} m {seconds} s"


def format_framerate(framerate: Any) -> str:
    """
    Render a framerate with three decimals, like '23.976 FPS' or '25.000 FPS'.

    MediaInfo itself always shows three decimals, so we match that for
    consistency (rather than collapsing 25.000 → 25). Returns "" when the
    framerate is missing.
    """
    if framerate is None or framerate == "":
        return ""
    try:
        return f"{float(framerate):.3f} FPS"
    except (TypeError, ValueError):
        return f"{framerate} FPS"


def format_bitrate(bitrate_bps: Any) -> str:
    """Render bitrate as Kb/s below 10 Mb/s, Mb/s above. Returns '0 Kb/s' on bad input."""
    try:
        br = int(bitrate_bps)
    except (TypeError, ValueError):
        br = 0
    if br >= 10_000_000:
        return f"{br / 1_000_000:.1f} Mb/s"
    return f"{br / 1000:.0f} Kb/s"


def format_bitrate_range(low_bps: Any, high_bps: Any) -> str:
    """
    Render a min–max bitrate range compactly in a single shared unit so it
    fits the narrow value column (e.g. "6.8–14.0 Mb/s" or "1500–2500 Kb/s").
    """
    try:
        low, high = int(low_bps), int(high_bps)
    except (TypeError, ValueError):
        return ""
    if high >= 10_000_000:
        return f"{low / 1_000_000:.1f}–{high / 1_000_000:.1f} Mb/s"
    return f"{low / 1000:.0f}–{high / 1000:.0f} Kb/s"


def get_language_full_name(code: Any) -> str:
    """
    Resolve an IETF/ISO language code to a compact display name.

    Returns the language name with the territory's *code* in parentheses when
    a region is known — "English (US)" rather than "English (United States)".
    This keeps the two-column rows from overflowing into each other.
    """
    if not code:
        return ""
    try:
        lang = Language.get(str(code))
        name = lang.language_name() or ""
        if name and lang.territory:
            return f"{name} ({lang.territory})"
        return name
    except (KeyError, ValueError, LookupError):
        return ""


def closest_standard_ratio(aspect_ratio: float,
                           tolerance: float = CLOSEST_RATIO_TOLERANCE) -> str:
    """
    Snap a numeric aspect ratio to the closest entry in STANDARD_RATIOS if
    they are within `tolerance`. Otherwise, return a small-denominator
    fraction (limit_denominator=20) to avoid absurd ratios like 172:93.
    """
    closest = min(STANDARD_RATIOS.items(), key=lambda x: abs(aspect_ratio - x[1]))
    if abs(aspect_ratio - closest[1]) < tolerance:
        return closest[0]
    fraction = Fraction(aspect_ratio).limit_denominator(20)
    return f"{fraction.numerator}:{fraction.denominator}"


def resolve_aspect_ratio(video_track: Any,
                         width: int,
                         height: int) -> str:
    """
    Return a clean display-aspect-ratio string.

    Strategy
    --------
    1. Prefer MediaInfo's pre-formatted "Display aspect ratio/String" field
       (`track.other_display_aspect_ratio[0]`). This is the same value the
       MediaInfo GUI shows in its inspector and handles non-square pixels.
    2. Otherwise, fall back to the numeric `display_aspect_ratio` and snap.
    3. As a last resort, compute width / height.
    """
    other = getattr(video_track, "other_display_aspect_ratio", None)
    if other and isinstance(other, (list, tuple)) and other[0]:
        return str(other[0]).strip()

    dar = getattr(video_track, "display_aspect_ratio", None)
    if dar is not None:
        try:
            return closest_standard_ratio(float(dar))
        except (TypeError, ValueError):
            pass

    if width and height:
        try:
            return closest_standard_ratio(width / height)
        except ZeroDivisionError:
            pass
    return ""


# =============================================================================
# 5. DATE UTILITIES
# =============================================================================

# MediaInfo emits dates in a handful of slightly different shapes depending on
# the muxer; this list covers the common ones.
DATE_INPUT_FORMATS: list[str] = [
    "%Y-%m-%d %H:%M:%S UTC",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "UTC %Y-%m-%d %H:%M:%S",
]


def normalize_date(raw: Any) -> str:
    """Normalize any MediaInfo timestamp to 'DD-MM-YYYY'. Returns 'N/A' on failure."""
    if not raw:
        return "N/A"
    text = str(raw).strip()
    if text.startswith("UTC "):
        text = text[4:]

    # Try just the date portion first
    date_only = text.split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_only, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue

    # Then try the full string against datetime formats
    for fmt in DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return "N/A"


def is_valid_release_date(s: str) -> bool:
    """True if `s` parses as DD-MM-YYYY."""
    try:
        datetime.strptime(s, "%d-%m-%Y")
        return True
    except (ValueError, TypeError):
        return False


# =============================================================================
# 6. HDR PARSING
# =============================================================================
# HDR metadata is the trickiest part of MediaInfo to format. A file may
# expose more than one HDR format simultaneously (e.g. Dolby Vision + HDR10+)
# and each format has up to ~7 attributes (version, profile, level,
# settings, compression, compatibility, …). In MediaInfo's machine-readable
# output every attribute is its own field, and multiple formats are encoded
# as " / "-separated values at the *same index* across all attribute fields.
#
# Concretely, for a DV + HDR10+ file:
#     HDR_Format                = "Dolby Vision / SMPTE ST 2094 App 4"
#     HDR_Format_Version        = "1.0 / 1"
#     HDR_Format_Profile        = "dvhe.08.06 / HDR10+ Profile B"
#     ...
#
# pymediainfo also exposes a pre-formatted human-friendly version under
# `track.other_hdr_format` (the "/String" variant in MediaInfo's API), which
# is what the MediaInfo GUI prints. We use that when present.

def extract_hdr_format(video_track: Any) -> Optional[str]:
    """Return a human-readable HDR description, or None if the track has none."""
    if video_track is None:
        return None

    # --- Strategy 1: pre-formatted string from MediaInfo (preferred) --------
    other = getattr(video_track, "other_hdr_format", None)
    if other and isinstance(other, (list, tuple)) and other[0]:
        return str(other[0]).strip()

    # --- Strategy 2: assemble from individual fields ------------------------
    formats        = field_list(video_track, "hdr_format")
    versions       = field_list(video_track, "hdr_format_version")
    profiles       = field_list(video_track, "hdr_format_profile")
    levels         = field_list(video_track, "hdr_format_level")
    settings       = field_list(video_track, "hdr_format_settings")
    compressions   = field_list(video_track, "hdr_format_compression")
    compatibilities = field_list(video_track, "hdr_format_compatibility")

    rendered: list[str] = []
    for i, fmt in enumerate(formats):
        if not fmt:
            continue
        chunks = [fmt]

        def part(values: list[str], prefix: str = "", suffix: str = "") -> None:
            """Append values[i] with optional decoration if non-empty."""
            if i < len(values) and values[i]:
                chunks.append(f"{prefix}{values[i]}{suffix}")

        part(versions,        prefix="Version ")
        part(profiles,        prefix="Profile ")
        part(levels,          prefix="Level ")
        part(settings)

        # "no metadata compression" reads better than the literal "None"
        if i < len(compressions) and compressions[i]:
            comp = compressions[i]
            chunks.append("no metadata compression" if comp.lower() == "none" else comp)

        part(compatibilities, suffix=" compatible")

        rendered.append(", ".join(chunks))

    return " / ".join(rendered) if rendered else None


# =============================================================================
# 6b. HDR-AWARE DEFAULT NOTE
# =============================================================================
# When a release contains HDR content (HDR10, HDR10+, Dolby Vision …) we want
# to warn downloaders to verify their playback chain before grabbing a 20 GB
# 4K file that won't play right on their TV. This helper inspects the HDR
# description produced by `extract_hdr_format` and returns a polished
# multi-format warning string, or None if no HDR was detected.

HDR_WARNING_TEMPLATE: str = (
    "WARNING: This release contains {formats} content. "
    "Please verify that your playback hardware is compatible before downloading."
)


def build_hdr_warning_note(hdr_format: Optional[str]) -> Optional[str]:
    """Return a hardware-compatibility warning when HDR formats are present."""
    if not hdr_format:
        return None

    lowered = hdr_format.lower()
    detected: list[str] = []
    if "dolby vision" in lowered:
        detected.append("Dolby Vision")
    # HDR10+ identifies itself by either the friendly name or its
    # ST 2094 App 4 standards reference.
    if "hdr10+" in lowered or "smpte st 2094" in lowered:
        detected.append("HDR10+")
    elif "hdr10" in lowered:
        detected.append("HDR10")

    if not detected:
        return None

    if len(detected) == 1:
        formats_str = detected[0]
    elif len(detected) == 2:
        formats_str = f"{detected[0]} + {detected[1]}"
    else:
        formats_str = ", ".join(detected[:-1]) + f" + {detected[-1]}"
    return HDR_WARNING_TEMPLATE.format(formats=formats_str)


# =============================================================================
# 7. CODEC LABEL DERIVATION
# =============================================================================

def derive_video_codec_label(format_name: Optional[str],
                             encoded_library: Optional[str],
                             source: Optional[str]) -> str:
    """
    Build a release-style codec label such as 'x265 - BluRay' or 'H264 - WEB-DL'.

    The prefix ('xNNN' vs 'HNNN') reflects whether the file was encoded by a
    known software library (xNNN) or simply muxed from a stream (HNNN).
    The suffix is derived from the source — BluRay / UHD BluRay get their
    own labels, everything else defaults to WEB-DL.
    """
    if not format_name:
        return ""
    suffix = SOURCE_DELIVERY_SUFFIX.get(source or "", DEFAULT_DELIVERY_SUFFIX)
    if format_name == "AVC":
        prefix = "x264" if encoded_library else "H264"
        return f"{prefix} - {suffix}"
    if format_name == "HEVC":
        prefix = "x265" if encoded_library else "H265"
        return f"{prefix} - {suffix}"
    return format_name


def normalize_audio_codec(raw: Optional[str]) -> str:
    """Apply AUDIO_CODEC_MAP, returning the raw value when no mapping exists."""
    if not raw:
        return ""
    return AUDIO_CODEC_MAP.get(raw, raw)


def normalize_audio_channels(raw: Any) -> str:
    """Convert a raw channel count to a label like '5.1' / '7.1' / 'Mono'."""
    if raw is None:
        return ""
    text = str(raw).strip()
    return AUDIO_CHANNELS_MAP.get(text, text)


# =============================================================================
# 8. BOX / TEXT FORMATTING (ASCII TEMPLATE PRIMITIVES)
# =============================================================================
# The NFO box is 75 characters wide:
#     `█` + 73 chars of content + `█`
# Helpers below ensure every line we emit fits that grid.
# -----------------------------------------------------------------------------

BOX_INNER_WIDTH: int = 73  # characters between the two `█` borders
BOX_CONTENT_WIDTH_WITH_PADDING: int = 71  # subtract 2 for the inner padding spaces
MIN_TWO_COLUMN_GAP: int = 2  # minimum spaces between the two columns in a row


# Characters at which `wrap_text` may break a line, in priority order.
# Spaces are preferred (most natural), then dots/underscores/dashes for
# scene-style filenames like `Project.Hail.Mary.2026.MULTi.VF2.iMAX-GRP`.
WRAP_BREAK_CHARS: tuple[str, ...] = (" ", ".", "_", "-")


def nfc(text: Any) -> str:
    """
    Normalize text to Unicode NFC (composed form).

    macOS filenames store accents *decomposed* (e.g. "é" = "e" + U+0301, two
    code points). `len()` then counts more characters than are visually
    rendered, which throws off every `.center()` / `.ljust()` in the box and
    pushes the right border out of alignment. NFC collapses such sequences to
    a single code point so width math matches what the user sees.
    """
    if text is None:
        return ""
    return unicodedata.normalize("NFC", str(text))


def wrap_text(text: str, width: int = 65) -> list[str]:
    """
    Greedy word-wrap that prefers breaking on spaces, but falls back to
    other natural delimiters (`.`, `_`, `-`) so long dot-separated filenames
    don't get hard-cut mid-token.

    Existing `\\n` characters in the input are treated as hard paragraph
    breaks — each paragraph is wrapped independently and empty paragraphs
    become empty lines. Without this, an AniList description containing
    embedded newlines would have those newlines bleed straight through the
    `█  | ... |  █` box border.
    """
    text = nfc(text)
    lines: list[str] = []

    paragraphs = text.split("\n")
    for index, paragraph in enumerate(paragraphs):
        if not paragraph:
            lines.append("")  # preserve blank paragraph as an empty row
            continue
        remaining = paragraph
        while len(remaining) > 0:
            if len(remaining) > width:
                cut = -1
                for ch in WRAP_BREAK_CHARS:
                    pos = remaining[:width + 1].rfind(ch)
                    if pos > cut:
                        cut = pos
                if cut <= 0:
                    # No break char found — hard cut at the width boundary.
                    cut = width
                    lines.append(remaining[:cut])
                    remaining = remaining[cut:]
                else:
                    lines.append(remaining[:cut].rstrip())
                    # Skip the break char itself only if it's whitespace.
                    remaining = (
                        remaining[cut + 1:] if remaining[cut] == " "
                        else remaining[cut:]
                    )
                    remaining = remaining.lstrip()
            else:
                lines.append(remaining)
                remaining = ""
        # Trailing newline → emit one extra empty row before the next paragraph
        # (only meaningful between paragraphs, not after the last one).
        if index < len(paragraphs) - 1:
            # The blank row will already be emitted when the next iteration
            # processes an empty paragraph; nothing to do here.
            pass
    return lines


def format_title_block(title: str) -> str:
    """
    Render the centered movie title row(s). The title is centered inside the
    71-char content area (padded by one space on each side from the border).
    Long titles are wrapped across multiple lines so they never overflow.
    """
    if not title:
        title = "N/A"
    lines = wrap_text(title, width=BOX_CONTENT_WIDTH_WITH_PADDING)
    return "\n".join(
        f"█ {line.center(BOX_CONTENT_WIDTH_WITH_PADDING)} █" for line in lines
    )


def format_filename_block(filename: str) -> str:
    """Same as format_title_block, but for the original filename row."""
    return format_title_block(filename)


def format_description(description: str) -> str:
    """
    Render the description rows. Long descriptions wrap; each line is wrapped
    inside `|` pipes for visual emphasis, mirroring the original template.

    Width math (75-wide outer box, 73-wide inner space):
        `█  | <65 chars> |  █`  →  1 + 2 + 1 + 1 + 65 + 1 + 1 + 2 + 1 = 75.
    """
    # 73 inner - 4 "  |" markers - 4 "|  " markers = 65 chars of text per line.
    inner = BOX_INNER_WIDTH - 8
    if description == "N/A":
        return f"█  | {description.center(inner)} |  █"
    wrapped = wrap_text(description, width=inner)
    return "\n".join(f"█  | {line.ljust(inner)} |  █" for line in wrapped)


def format_attachments(attachments: str) -> str:
    """Render the attachments row(s), centered. Wraps long lists across lines."""
    attachments = nfc(attachments)
    width = BOX_INNER_WIDTH - 2  # inner content area for attachments
    if len(attachments) <= width - 4:
        return f"█   {attachments.center(width - 4)}   █"
    wrapped = wrap_text(attachments, width=width - 4)
    return "\n".join(f"█   {line.center(width - 4)}   █" for line in wrapped)


def format_two_columns(label_left: str, value_left: str,
                       label_right: str, value_right: str,
                       *,
                       label_width: int = 18,
                       total_width: int = BOX_INNER_WIDTH,
                       left_padding: int = 3) -> str:
    """
    Render a two-column row inside the box.

    The right column normally starts at column 40 so columns line up across
    rows. When the left value is too wide to fit before that anchor (for
    example a long language string like "English (United States)") we keep
    a MIN_TWO_COLUMN_GAP-space gap and let the right column push right. If
    the total still overflows, we truncate the right value rather than
    breaking the box border.
    """
    right_start = 40
    left  = f"{label_left.ljust(label_width)}{value_left}"
    right = f"{label_right.ljust(label_width)}{value_right}"

    # 1. Determine the gap. Honour the right-column anchor when possible,
    #    fall back to a minimum so the columns never touch.
    gap = max(MIN_TWO_COLUMN_GAP, right_start - left_padding - len(left))

    # 2. Compute the right padding, truncating right value if we'd overflow.
    used = left_padding + len(left) + gap + len(right)
    if used > total_width:
        excess = used - total_width
        # Trim the right *value* (not the label) by `excess` chars.
        if len(value_right) > excess:
            value_right = value_right[:-excess].rstrip()
            right = f"{label_right.ljust(label_width)}{value_right}"
            used = left_padding + len(left) + gap + len(right)
        # If the row is still too wide (e.g., huge label), as a last resort
        # cap the gap at the minimum and hard-truncate.
        if used > total_width:
            overflow = used - total_width
            right = right[:max(0, len(right) - overflow)]
            used = left_padding + len(left) + gap + len(right)

    right_padding = max(0, total_width - used)
    return (
        f"█{' ' * left_padding}{left}{' ' * gap}{right}{' ' * right_padding}█"
    )


def format_centered_block(content: str, width: int = BOX_CONTENT_WIDTH_WITH_PADDING) -> str:
    """Word-wrap and center each line inside the box."""
    wrapped = wrap_text(content, width)
    return "\n".join(f"█ {line.center(width)} █" for line in wrapped)


def format_single_centered_line(content: str,
                                *,
                                width: int = BOX_CONTENT_WIDTH_WITH_PADDING,
                                left_padding: int = 2,
                                right_padding_adjustment: int = 2) -> str:
    """Render a single centered content line with explicit padding control."""
    total_width = width + left_padding
    adjusted = content.center(total_width - left_padding - 2)
    return f"█{' ' * left_padding}{adjusted}{' ' * right_padding_adjustment}█"


# Cached separator lines used many times across the template
_EMPTY_ROW = "█                                                                         █"
_HEAVY_RULE = "███████████████████████████████████████████████████████████████████████████"


def format_label_value_row(label: str, value: str, *,
                           label_width: int = 18,
                           total_width: int = BOX_INNER_WIDTH,
                           left_padding: int = 3) -> str:
    """
    Render a single 'LABEL.........:  value' row, padded to fit the 75-wide box.

    Used by the LiNKS section. The label is padded to `label_width` so a
    consistent gap sits between it and the value (matching the two-column
    rows elsewhere). Values that overflow the row width are truncated rather
    than wrapping — URLs look ugly broken across lines.
    """
    label_text = label.ljust(label_width)
    content = f"{label_text}{value}"
    inner_capacity = total_width - left_padding
    if len(content) > inner_capacity:
        content = content[:inner_capacity]
    right_padding = inner_capacity - len(content)
    return f"█{' ' * left_padding}{content}{' ' * right_padding}█"


def format_links_section(links: dict) -> str:
    """
    Render the LiNKS section header + rows.

    `links` is an ordered dict of {label: url}. Empty / None values are
    skipped so the user can leave fields blank in the GUI. Returns an empty
    string when no link is present (the caller skips the whole section).

    Labels are pre-formatted with trailing dots, e.g. "TMDB...........: ".
    """
    # Build "LABEL...: url" strings (truncate any that overflow the box).
    texts = [
        f"{label} {url}"[:BOX_INNER_WIDTH]
        for label, url in links.items()
        if url not in (None, "")
    ]
    if not texts:
        return ""

    # Center the whole block: every row shares the left margin of the widest
    # row, so the labels stay aligned *and* the group sits centered in the box.
    widest = max(len(t) for t in texts)
    left_margin = max(0, (BOX_INNER_WIDTH - widest) // 2)
    rows = [
        f"█{' ' * left_margin}{t.ljust(BOX_INNER_WIDTH - left_margin)}█"
        for t in texts
    ]

    # No leading rule here: the previous section already closes with one, so
    # we start straight at the title band (matching the AUDiO/SUBTiTLES style).
    return (
        f"\n█████████████████▓▓▓▒▒▒░░░         LiNKS         ░░░▒▒▒▓▓▓█████████████████\n"
        f"{_HEAVY_RULE}\n"
        f"{_EMPTY_ROW}\n"
        + "\n".join(rows) + "\n"
        f"{_EMPTY_ROW}"
    )


# Standard label names for the LiNKS section. Order matters — this is the
# order they'll appear in the rendered output. Each label is exactly 16
# characters wide so the colons line up visually with the other rows.
DEFAULT_LINK_LABELS: tuple[str, ...] = (
    "TMDB...........:",
    "TVDB...........:",
    "iMDB...........:",
    "ANiLiST........:",
    "MAL............:",
)

# Which labels each lookup provider is allowed to overwrite. A TMDB fetch
# clears + fills the TMDB / TVDB / iMDB rows; an AniList fetch clears + fills
# AniList / MAL. The two providers therefore coexist in the form without one
# wiping the other's results when the user switches between them.
TMDB_MANAGED_LABELS: tuple[str, ...] = (
    "TMDB...........:",
    "TVDB...........:",
    "iMDB...........:",
)
ANILIST_MANAGED_LABELS: tuple[str, ...] = (
    "ANiLiST........:",
    "MAL............:",
)
TVDB_MANAGED_LABELS: tuple[str, ...] = (
    "TVDB...........:",
)

