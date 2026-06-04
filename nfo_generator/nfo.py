"""Metadata aggregation (``collect_metadata``), ASCII banner, and the ``build_nfo`` renderer.

See ``nfo_generator.core`` for shared constants and helpers.
"""

from __future__ import annotations

# Re-export everything from core so we can write code at the same "level
# of abstraction" the single-file version did. Star-import is safe here:
# ``core`` is the only module that does it and is well-curated.
from .core import *  # noqa: F401, F403
from .core import (   # explicit names also bring in private helpers used inline
    logger,
    _setup_logger,
    _HEAVY_RULE,
    _EMPTY_ROW,
    MEDIAINFO_LIB_CANDIDATES,)
from .types import MetaDict




# =============================================================================
# 9. METADATA AGGREGATION
# =============================================================================

# Folder-mode aggregation strategies (how to combine per-episode values).
FOLDER_BITRATE_MODES = ("average", "range", "na")
FOLDER_DURATION_MODES = ("total", "average", "na")


def collect_metadata(path: str,
                     *,
                     bitrate_mode: str = "average",
                     duration_mode: str = "total") -> MetaDict:
    """
    Return a normalized metadata dict for either a single file or a folder.

    - A **file** path → metadata for that file.
    - A **folder** path → aggregated metadata across all videos inside it
      (sizes summed; duration/bitrate combined per `duration_mode` /
      `bitrate_mode`). Useful for a whole-season .nfo.

    The returned dict is the single source of truth consumed by `build_nfo()`
    and the GUI/CLI preview panels, using primitive types only.
    """
    if os.path.isdir(path):
        return collect_folder_metadata(
            path, bitrate_mode=bitrate_mode, duration_mode=duration_mode,
        )
    return _collect_file_metadata(path)


def _collect_file_metadata(file_path: str) -> dict:
    """
    Parse a single video file and return a normalized metadata dict.

    Raises
    ------
    LibMediaInfoNotFound : libmediainfo can't be loaded.
    FileNotFoundError    : `file_path` doesn't exist.
    ValueError           : the file has no usable Video / General tracks.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)

    media = parse_media(file_path)

    general = next((t for t in media.tracks if t.track_type == "General"), None)
    video   = next((t for t in media.tracks if t.track_type == "Video"),   None)
    audios  = [t for t in media.tracks if t.track_type == "Audio"]
    subs    = [t for t in media.tracks if t.track_type == "Text"]
    menu    = next((t for t in media.tracks if t.track_type == "Menu"),    None)

    if general is None:
        raise ValueError("No 'General' track found — invalid media file.")
    if video is None:
        raise ValueError("No 'Video' track found — not a video file?")

    # ---- General -------------------------------------------------------------
    file_size           = format_size(field(general, "file_size"))
    file_modified_date  = normalize_date(
        field(general, "file_last_modification_date", "file_modified_date")
    )
    title       = field(general, "title", "movie_name", default="") or ""
    description = field(general, "description")

    # Attachments come from MediaInfo's "extra" block; pymediainfo flattens
    # them onto the General track. They are typically " / "-separated.
    attachments_raw = field(general, "attachments")
    attachments = (
        ", ".join(str(attachments_raw).split(" / ")) if attachments_raw else None
    )

    # ---- Video ---------------------------------------------------------------
    video_format    = field(video, "format")
    encoded_library = field(video, "encoded_library", "writing_library")
    video_bitrate   = format_bitrate(field(video, "bit_rate", default=0))

    try:
        width  = int(field(video, "width",  default=0) or 0)
        height = int(field(video, "height", default=0) or 0)
    except (TypeError, ValueError):
        width = height = 0

    resolution    = f"{width} x {height}" if width and height else ""
    aspect_ratio  = resolve_aspect_ratio(video, width, height)
    framerate     = format_framerate(field(video, "frame_rate"))
    duration      = format_duration(field(video, "duration"))
    hdr_format    = extract_hdr_format(video)
    chapters      = "YES" if menu is not None else "N/A"

    # ---- Audio tracks --------------------------------------------------------
    audio_entries: list[dict] = []
    for track in audios:
        audio_entries.append({
            "language": get_language_full_name(field(track, "language")),
            "channels": normalize_audio_channels(
                field(track, "channel_s", "channels")
            ),
            "codec":    normalize_audio_codec(
                field(track, "commercial_name", "format_commercial_ifany", "format")
            ),
            "bitrate":  format_bitrate(field(track, "bit_rate", default=0)),
        })

    # ---- Subtitle tracks -----------------------------------------------------
    subtitle_entries: list[dict] = []
    for track in subs:
        s_format = field(track, "format", default="") or ""
        if s_format == "UTF-8":
            s_format = "SRT"

        title_str = str(field(track, "title", default="") or "")
        if "Forced" in title_str:
            s_type = "Forced"
        elif "SDH" in title_str:
            s_type = "SDH"
        else:
            # Default to Full unless the title gives us a stronger hint.
            s_type = "Full"

        subtitle_entries.append({
            "language": get_language_full_name(field(track, "language")),
            "type":     s_type,
            "format":   s_format,
        })

    return {
        # General
        "file_size":          file_size,
        "file_modified_date": file_modified_date,
        "title":              title,
        "description":        description,
        "description_source": "",   # set by the GUI/CLI (e.g. "TMDB") when known
        "attachments":        attachments,
        # Video
        "video_format":       video_format,
        "encoded_library":    encoded_library,
        "video_bitrate":      video_bitrate,
        "resolution":         resolution,
        "aspect_ratio":       aspect_ratio,
        "framerate":          framerate,
        "duration":           duration,
        "hdr_format":         hdr_format,
        "writing_library":    encoded_library,
        "chapters":           chapters,
        # Row labels (folder mode overrides these to "TOTAL RUNTiME" etc.)
        "duration_label":     "DURATiON.......:",
        "bitrate_label":      "BiTRATE........:",
        # Tracks
        "audios":             audio_entries,
        "subtitles":          subtitle_entries,
        # Folder-mode flags (always present so build_nfo can rely on them)
        "is_folder":          False,
        "video_count":        1,
        "folder_summary":     "",
    }


def list_video_files(folder_path: str) -> list[str]:
    """Return the sorted video files directly inside `folder_path`."""
    entries = []
    for name in os.listdir(folder_path):
        full = os.path.join(folder_path, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
            entries.append(full)
    return sorted(entries)


def collect_folder_metadata(folder_path: str,
                            *,
                            bitrate_mode: str = "average",
                            duration_mode: str = "total") -> MetaDict:
    """
    Aggregate metadata across every video file in `folder_path`.

    The first file provides the template (codec, resolution, audio/subtitle
    layout, HDR…), assumed consistent across a season. Then:
      - **size**     = sum of all file sizes
      - **duration** = total / average / blank   (per `duration_mode`)
      - **bitrate**  = average / min–max range / blank  (per `bitrate_mode`)
      - **title**    = the folder's name

    Raises ValueError when the folder contains no recognised video files.
    """
    video_files = list_video_files(folder_path)
    if not video_files:
        raise ValueError(f"No video files found in folder: {folder_path}")

    # Template from the first episode.
    base = _collect_file_metadata(video_files[0])

    total_bytes = 0
    total_ms = 0.0
    durations_seen = 0
    bitrates: list[int] = []

    for video_path in video_files:
        media = parse_media(video_path)
        general = next((t for t in media.tracks if t.track_type == "General"), None)
        video = next((t for t in media.tracks if t.track_type == "Video"), None)

        try:
            total_bytes += int(field(general, "file_size", default=0) or 0)
        except (TypeError, ValueError):
            pass
        try:
            ms = float(field(video, "duration", default=0) or 0)
            if ms > 0:
                total_ms += ms
                durations_seen += 1
        except (TypeError, ValueError):
            pass
        try:
            br = int(field(video, "bit_rate", default=0) or 0)
            if br > 0:
                bitrates.append(br)
        except (TypeError, ValueError):
            pass

    # ---- Size: always the sum --------------------------------------------
    base["file_size"] = format_size(total_bytes)

    # ---- Duration: the aggregation method is encoded in the ROW LABEL -----
    # (e.g. "TOTAL RUNTiME..:" / "AVG RUNTiME....:") so it reads naturally in
    # the box instead of needing a separate explanatory line.
    if duration_mode == "total":
        base["duration"] = format_duration(total_ms)
        base["duration_label"] = "TOTAL RUNTiME..:"
    elif duration_mode == "average" and durations_seen:
        base["duration"] = format_duration(total_ms / durations_seen)
        base["duration_label"] = "AVG RUNTiME....:"
    elif duration_mode == "na":
        base["duration"] = ""
        base["duration_label"] = "DURATiON.......:"
    # else: keep the first file's duration + default label

    # ---- Bitrate: same idea — the label carries the meaning ---------------
    if bitrate_mode == "average" and bitrates:
        base["video_bitrate"] = format_bitrate(sum(bitrates) / len(bitrates))
        base["bitrate_label"] = "AVG BiTRATE....:"
    elif bitrate_mode == "range" and bitrates:
        low, high = min(bitrates), max(bitrates)
        if low == high:
            base["video_bitrate"] = format_bitrate(low)
            base["bitrate_label"] = "BiTRATE........:"
        else:
            base["video_bitrate"] = format_bitrate_range(low, high)
            base["bitrate_label"] = "BiTRATE RANGE..:"
    elif bitrate_mode == "na":
        base["video_bitrate"] = ""
        base["bitrate_label"] = "BiTRATE........:"
    # else: keep the first file's bitrate + default label

    # ---- Folder flags + a short summary line (episode count only) --------
    # The folder name is already shown on the filename row, so the title is
    # left empty (no duplicate). The summary line just states the pack size.
    base["title"] = ""
    base["is_folder"] = True
    base["video_count"] = len(video_files)
    count = len(video_files)
    base["folder_summary"] = f"Season pack · {count} file{'s' if count != 1 else ''}"
    return base


# =============================================================================
# 9b. ASCII BANNER GENERATION (pyfiglet)
# =============================================================================
# The header & footer of the NFO are big ASCII-art banners generated from
# arbitrary text via pyfiglet — that lets any team or persona re-skin the
# output without touching the source code.
#
# When the user provides no text, the header defaults to a generic
# "NOGROUP" so the script ships with no implicit branding. The footer
# defaults to empty (no banner art rendered at all), which makes the bottom
# of the NFO look clean by default.
#
# The generated lines are then framed to fit the 75-wide box: each line is
# centered inside the 73-char content area and wrapped between `█` borders.

# Generic placeholder used when the script runs out-of-the-box.
DEFAULT_HEADER_TEXT: str = "NOGROUP"
DEFAULT_FOOTER_TEXT: str = ""  # empty → no footer banner rendered

# pyfiglet font choices that look good with the existing box style. Listed
# in roughly increasing visual weight. "block" is the closest to the
# block look; "ansi_shadow" gives a slightly chunkier feel.
PYFIGLET_FONT_CHOICES: tuple[str, ...] = (
    "block", "ansi_shadow", "ansi_regular", "big", "banner3",
)
DEFAULT_PYFIGLET_FONT: str = "block"

# Characters used to render pyfiglet output. The library outputs `#` and a
# few other ASCII glyphs as fill; we replace them with the same block
# character used in the box borders so the visual style stays cohesive.
_PYFIGLET_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("#", "█"), ("$", "█"), ("@", "█"), ("&", "█"),
)


def _strip_blank_lines(lines: list[str]) -> list[str]:
    """Drop leading and trailing lines that contain only whitespace."""
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def render_ascii_banner(text: str,
                        *,
                        font: str = DEFAULT_PYFIGLET_FONT,
                        width: int = BOX_INNER_WIDTH) -> str:
    """
    Render arbitrary text as an ASCII banner that fits inside the NFO box.

    Returns a multi-line string where every line is bordered by `█ ... █`
    (75 chars wide). pyfiglet adds 1–2 blank rows of vertical padding above
    and below the rendered text by default, which makes the spacing between
    the banner and the surrounding section dividers look excessive; we
    strip those blank rows so the banner sits tightly against the box's
    decorative lines.

    Returns an empty string when pyfiglet is missing, the font is invalid,
    or `text` is empty — the caller decides whether to emit a banner at all.
    """
    if not text or not text.strip():
        return ""
    try:
        import pyfiglet  # optional dependency, imported lazily
    except ImportError:
        # Without pyfiglet we can't render art. Return a simple centered
        # text line so the section still has *something* — better than a
        # silently-missing banner.
        return f"█{text.strip().center(width)}█"

    try:
        figlet = pyfiglet.Figlet(font=font, width=width)
        raw = figlet.renderText(text.strip()).rstrip("\n")
    except Exception:  # noqa: BLE001 — bad font name, etc.
        return f"█{text.strip().center(width)}█"

    # Replace pyfiglet's default fill glyphs with the block character so
    # the banner matches the box's `█` borders.
    for src, dst in _PYFIGLET_REPLACEMENTS:
        raw = raw.replace(src, dst)

    # Split, strip outer padding, frame each remaining row inside borders.
    lines = _strip_blank_lines(raw.split("\n"))
    framed: list[str] = []
    for line in lines:
        if len(line) > width:
            # Truncate runaway rendering (very wide fonts) — we don't want
            # to break the box width on uppercase 10-letter inputs.
            line = line[:width]
        framed.append(f"█{line.center(width)}█")
    return "\n".join(framed)


def resolve_banner(text: Optional[str], default_text: str,
                   *, font: str = DEFAULT_PYFIGLET_FONT) -> str:
    """
    Resolve banner text to its rendered ASCII art.

    `text` is the user-supplied custom text (may be None or empty).
    `default_text` is the generic placeholder used when no custom text is
    provided; pass "" to suppress the banner entirely.
    """
    chosen = (text or "").strip() or default_text
    if not chosen:
        return ""
    return render_ascii_banner(chosen, font=font)


# =============================================================================
# 10. NFO TEMPLATE BUILDER
# =============================================================================

# Default note written in the bottom banner when the user provides none.
DEFAULT_NOTE: str = "ENJOY  THE  SHOW !"


def build_nfo(video_path: str,
              meta: MetaDict,
              *,
              release_date: str,
              source: str,
              hardcoded_subs: Optional[list[dict]] = None,
              note: str = DEFAULT_NOTE,
              links: Optional[dict] = None,
              header_text: Optional[str] = None,
              footer_text: Optional[str] = None,
              banner_font: str = DEFAULT_PYFIGLET_FONT,
              raw_mediainfo: Optional[str] = None) -> str:
    """
    Render the final .nfo string from extracted metadata + user choices.

    Parameters
    ----------
    video_path     : path to the source media file (used for the filename row).
    meta           : the dict returned by `collect_metadata()`.
    release_date   : DD-MM-YYYY release date.
    source         : release source label (e.g. "Netflix (NF)", "BluRay").
    hardcoded_subs : optional list of {"language", "type"} dicts, used only
                     when the file has no muxed subtitle tracks.
    note           : freeform note rendered in the bottom banner section.
                     Defaults to DEFAULT_NOTE.
    links          : optional ordered dict of {label: url} for the LiNKS
                     section. Empty / None values are dropped; an empty dict
                     hides the section entirely.
    header_text    : optional custom text to render as the top banner. When
                     given, it is rendered via pyfiglet; otherwise the
                     default placeholder banner is used.
    footer_text    : optional custom text for the bottom banner; defaults
                     to the "03" art.
    banner_font    : pyfiglet font name to use for both banners.
    """
    filename = os.path.basename(video_path)
    video_codec_label = derive_video_codec_label(
        meta.get("video_format"),
        meta.get("encoded_library"),
        source,
    )
    header_banner = resolve_banner(header_text, DEFAULT_HEADER_TEXT, font=banner_font)
    footer_banner = resolve_banner(footer_text, DEFAULT_FOOTER_TEXT, font=banner_font)

    # The movie/episode title row is only rendered when MediaInfo actually
    # carries a title — otherwise we skip it entirely (no blank "N/A" line).
    title = (meta.get("title") or "").strip()
    title_block = f"{format_title_block(title)}\n{_EMPTY_ROW}\n" if title else ""

    # Folder/season releases get a centered summary line explaining how the
    # aggregated size/runtime/bitrate figures were combined.
    summary = (meta.get("folder_summary") or "").strip()
    summary_block = f"{format_centered_block(summary)}\n{_EMPTY_ROW}\n" if summary else ""

    # ---- Header banner & RELEASE DETAILS ------------------------------------
    nfo = f"""
█▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀█
{_EMPTY_ROW}
{header_banner}
{_EMPTY_ROW}
{_HEAVY_RULE}
█████████████████▓▓▓▒▒▒░░░    RELEASE DETAiLS    ░░░▒▒▒▓▓▓█████████████████
{_HEAVY_RULE}
{_EMPTY_ROW}
{format_filename_block(filename)}
{_EMPTY_ROW}
{title_block}{summary_block}{format_two_columns("RELEASE SiZE...:", meta["file_size"],
                    "RELEASE DATE...:", release_date)}
{_EMPTY_ROW}
{format_two_columns("SOURCE.........:", source,
                    "CHAPTERS.......:", meta["chapters"])}
{_EMPTY_ROW}"""

    # ---- Optional DESCRIPTION & ATTACHMENTS sections ------------------------
    if meta.get("description"):
        desc_source = (meta.get("description_source") or "").strip()
        source_line = (
            f"\n{format_centered_block(f'Description Source: {desc_source}')}"
            if desc_source else ""
        )
        nfo += f"""
█                               DESCRiPTiON                               █
{format_description(meta["description"])}{source_line}
{_EMPTY_ROW}"""

    if meta.get("attachments"):
        nfo += f"""
█                               ATTACHMENTS                               █
{format_attachments(meta["attachments"])}
{_EMPTY_ROW}"""

    # ---- VIDEO INFO ----------------------------------------------------------
    nfo += f"""
{_HEAVY_RULE}
█████████████████▓▓▓▒▒▒░░░      ViDEO  iNFO      ░░░▒▒▒▓▓▓█████████████████
{_HEAVY_RULE}
{_EMPTY_ROW}
{format_two_columns("CODEC..........:", video_codec_label,
                    meta.get("bitrate_label", "BiTRATE........:"), meta["video_bitrate"])}
{_EMPTY_ROW}
{format_two_columns("RESOLUTiON.....:", meta["resolution"],
                    "ASPECT RATiO...:", meta["aspect_ratio"])}
{_EMPTY_ROW}
{format_two_columns("FRAMERATE......:", meta["framerate"],
                    meta.get("duration_label", "DURATiON.......:"), meta["duration"])}
{_EMPTY_ROW}"""

    if meta.get("hdr_format"):
        nfo += f"""
█                               HDR  FORMAT                               █
{format_centered_block(meta["hdr_format"])}
{_EMPTY_ROW}"""

    if meta.get("writing_library"):
        nfo += f"""
█                             WRiTiNG LiBRARY                             █
{format_single_centered_line(meta["writing_library"])}
{_EMPTY_ROW}"""

    nfo += f"\n{_HEAVY_RULE}"

    # ---- AUDIO sections ------------------------------------------------------
    audio_sections: list[str] = []
    for i, a in enumerate(meta["audios"], start=1):
        audio_sections.append(f"""
█████████████████▓▓▓▒▒▒░░░       AUDiO  #{i}       ░░░▒▒▒▓▓▓█████████████████
{_HEAVY_RULE}
{_EMPTY_ROW}
{format_two_columns("LANGUAGE.......:", a["language"], "CHANNELS.......:", a["channels"])}
{_EMPTY_ROW}
{format_two_columns("CODEC..........:", a["codec"], "BiTRATE........:", a["bitrate"])}
{_EMPTY_ROW}
{_HEAVY_RULE}""")
    nfo += "".join(audio_sections)

    # ---- SUBTITLES sections --------------------------------------------------
    subtitle_sections: list[str] = []
    if not meta["subtitles"] and hardcoded_subs:
        # Hardcoded subs path — the file has no muxed subtitle tracks but the
        # user told us subs are burned into the picture.
        for i, h in enumerate(hardcoded_subs, start=1):
            subtitle_sections.append(f"""
█████████████████▓▓▓▒▒▒░░░     SUBTiTLES  #{i}     ░░░▒▒▒▓▓▓█████████████████
{_HEAVY_RULE}
{_EMPTY_ROW}
{format_two_columns("LANGUAGE.......:", h["language"], "TYPE...........:", h["type"])}
{_EMPTY_ROW}
{format_single_centered_line("FORMAT..........: HARDCODED")}
{_EMPTY_ROW}
{_HEAVY_RULE}""")
    else:
        for i, s in enumerate(meta["subtitles"], start=1):
            subtitle_sections.append(f"""
█████████████████▓▓▓▒▒▒░░░     SUBTiTLES  #{i}     ░░░▒▒▒▓▓▓█████████████████
{_HEAVY_RULE}
{_EMPTY_ROW}
{format_two_columns("LANGUAGE.......:", s["language"], "TYPE...........:", s["type"])}
{_EMPTY_ROW}
{format_single_centered_line(f"FORMAT..........: {s['format']}")}
{_EMPTY_ROW}
{_HEAVY_RULE}""")
    nfo += "".join(subtitle_sections)

    # ---- LiNKS (optional) ---------------------------------------------------
    if links:
        nfo += format_links_section(links)

    # ---- NOTE -- multi-line capable so HDR warnings can wrap nicely --------
    # `format_centered_block` will produce a single centered line for short
    # notes (preserving the original look) and wrap onto two/three lines for
    # longer HDR warnings.
    nfo += f"""
{_HEAVY_RULE}
█████████████████▓▓▓▒▒▒░░░        NOTE(S)        ░░░▒▒▒▓▓▓█████████████████
{_HEAVY_RULE}
{_EMPTY_ROW}
{format_centered_block(note)}
{_EMPTY_ROW}
{_HEAVY_RULE}"""

    # ---- Footer banner (optional) + closing border -------------------------
    # When the footer text is empty, we skip the banner block entirely so the
    # box closes cleanly without a stray blank row.
    bottom_border = "█▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄█"
    if footer_banner:
        # No leading rule here — the NOTE section already closed with one.
        nfo += f"""
{_EMPTY_ROW}
{footer_banner}
{_EMPTY_ROW}
{bottom_border}"""
    else:
        nfo += f"\n{bottom_border}"

    # ---- Optional raw MediaInfo dump (plain text, below the box) -----------
    if raw_mediainfo:
        nfo += f"\n\n\n{raw_mediainfo.strip()}\n"

    return nfo


