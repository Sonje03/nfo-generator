"""TypedDict declarations for the metadata dict produced by `collect_metadata`.

These types are documentation-first: every field that ``build_nfo`` looks
up is described here, with the value type Tk/the renderer expects. The
GUI doesn't *require* these (it accesses fields through ``.get()``), but
IDEs and ``mypy`` will use them to catch typos and missing keys.

Why `total=False`? Folder mode adds extra keys (``folder_summary``,
``video_count`` …) and skips a few file-only ones, so making every field
required would produce false positives. The fields below are *expected*
but not strictly mandatory.
"""

from __future__ import annotations

from typing import TypedDict


class AudioTrack(TypedDict, total=False):
    """One entry in ``MetaDict["audios"]`` — produced per audio track."""

    language: str   # full language name, e.g. "English (US)"
    channels: str   # e.g. "5.1", "2.0", "Stereo"
    codec: str      # e.g. "AC-3", "E-AC-3 JOC", "AAC LC"
    bitrate: str    # formatted, e.g. "640 Kb/s"


class SubtitleTrack(TypedDict, total=False):
    """One entry in ``MetaDict["subtitles"]`` — produced per subtitle stream."""

    language: str   # full language name
    type: str       # "Full", "Forced", "SDH", "Commentary" …
    format: str     # "PGS", "ASS/SSA", "SRT", "VobSub" …


class LinksDict(TypedDict, total=False):
    """Optional per-row URLs in the LiNKS section."""

    TMDB: str
    TVDB: str
    iMDB: str
    ANiLiST: str
    MAL: str


class MetaDict(TypedDict, total=False):
    """
    The single source of truth shared between ``collect_metadata`` (the
    producer) and ``build_nfo`` (the consumer).

    Every field is optional at the type level because folder mode and the
    GUI populate slightly different subsets, but the renderer treats
    missing values as empty strings.
    """

    # --- General ----------------------------------------------------------
    file_size: str            # "1.42 GiB"
    file_modified_date: str   # "DD-MM-YYYY"
    title: str
    description: str
    description_source: str   # e.g. "TMDB", "AniList", "Crunchyroll", ""

    # --- Video ------------------------------------------------------------
    video_format: str         # codec name, e.g. "AVC", "HEVC"
    encoded_library: str      # encoder string, e.g. "x265 4.0+45"
    writing_library: str      # same value, kept for symmetry with MediaInfo
    video_bitrate: str
    resolution: str           # "1920x1080"
    aspect_ratio: str         # "16:9", "2.39:1"
    framerate: str            # "23.976 FPS"
    duration: str             # "01h32m14s"
    hdr_format: str           # full HDR metadata blurb (may be empty)
    chapters: str             # "YES" | "NO" | "N/A"

    # --- Row labels (folder mode swaps these) -----------------------------
    duration_label: str       # "DURATiON.......:" or "TOTAL RUNTiME..:"
    bitrate_label: str        # "BiTRATE........:" or "AVG BiTRATE...:"

    # --- Tracks -----------------------------------------------------------
    audios: list[AudioTrack]
    subtitles: list[SubtitleTrack]

    # --- Attachments (fonts, cover, etc.) ---------------------------------
    attachments: str          # comma-separated filenames

    # --- Folder-mode metadata ---------------------------------------------
    is_folder: bool
    video_count: int
    folder_summary: str       # "Season pack · 12 files"

    # --- Provider hooks (filled by the GUI when known, not by collect) ---
    links: LinksDict


__all__ = ["AudioTrack", "SubtitleTrack", "LinksDict", "MetaDict"]
