"""Release-folder generation, ffmpeg-based sample / subs / screenshots extraction, and the CLI workflow.

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

from .nfo import build_nfo, collect_metadata, collect_folder_metadata, render_ascii_banner
from .providers import tmdb_lookup, tmdb_search_candidates



# =============================================================================
# 11. RELEASE FOLDER GENERATION
# =============================================================================

DEFAULT_SUBFOLDERS: tuple[str, ...] = ("Sample", "Subs", "Screens")


def release_name_from_video(video_path: str) -> str:
    """Return the video file stem (e.g. 'Release.Name.x265-GROUP')."""
    return os.path.splitext(os.path.basename(video_path))[0]


def generate_release_folder(video_path: str,
                            nfo_content: str,
                            *,
                            with_subfolders: bool = False,
                            move_video: bool = False,
                            subfolders: Iterable[str] = DEFAULT_SUBFOLDERS,
                            extract_sample_flag: bool = False,
                            sample_duration: int = 60,
                            extract_subs_flag: bool = False,
                            generate_screens_flag: bool = False,
                            screens_count: int = 5,
                            meta: Optional[dict] = None,
                            progress_callback: Optional[Callable[[str, float], None]] = None) -> dict:
    """
    Create a wrapper folder around the release, optionally populated with a
    sample / extracted subtitles / generated screenshots.

    Layout::

        <parent>/<release_name>/
            <release_name>.nfo
            <release_name>.<ext>       (only if move_video=True)
            Sample/  Subs/  Screens/   (only if with_subfolders=True)

    When `with_subfolders` is True and the corresponding `*_flag` is set,
    each subfolder gets populated with actual content via ffmpeg
    (see `extract_sample`, `extract_subtitles`, `generate_screenshots`).
    Extraction is best-effort — failures don't abort the rest of the run.

    Returns
    -------
    dict
        {
            "folder": ..., "nfo": ..., "video": ...,
            "sample": <path or None>,
            "subs":   [paths...],
            "screens":[paths...],
        }
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(video_path)

    def _report(label: str, fraction: float) -> None:
        """Forward a progress update to the caller (best-effort)."""
        if progress_callback:
            try:
                progress_callback(label, fraction)
            except Exception:  # noqa: BLE001
                pass

    parent = os.path.dirname(os.path.abspath(video_path))
    release_name = release_name_from_video(video_path)
    folder = os.path.join(parent, release_name)

    if os.path.exists(folder) and not os.path.isdir(folder):
        raise FileExistsError(f"A non-folder already exists at {folder}")
    os.makedirs(folder, exist_ok=True)

    # Create subfolders up front (they're useful even when extraction is off).
    if with_subfolders:
        for sub in subfolders:
            os.makedirs(os.path.join(folder, sub), exist_ok=True)

    # Write the .nfo before any heavy extraction so the user gets the main
    # deliverable even if ffmpeg work is slow or fails midway.
    _report("Writing NFO…", 0.05)
    nfo_path = os.path.join(folder, f"{release_name}.nfo")
    with open(nfo_path, "w", encoding="utf-8") as fh:
        fh.write(nfo_content)

    # Optional ffmpeg-based content extraction. Skipped silently when the
    # corresponding subfolder isn't included or ffmpeg isn't available.
    sample_path: Optional[str] = None
    sub_paths: list[str] = []
    screen_paths: list[str] = []

    if with_subfolders:
        if extract_sample_flag:
            _report("Extracting sample…", 0.20)
            sample_dir = os.path.join(folder, "Sample")
            sample_path = extract_sample(
                video_path, sample_dir,
                duration_seconds=sample_duration, meta=meta,
            )
        if extract_subs_flag:
            _report("Extracting subtitles…", 0.45)
            sub_paths = extract_subtitles(video_path, os.path.join(folder, "Subs"))
        if generate_screens_flag:
            _report("Generating screenshots…", 0.60)
            screen_paths = generate_screenshots(
                video_path, os.path.join(folder, "Screens"),
                count=screens_count, meta=meta,
                progress_callback=lambda i, n: _report(
                    f"Screenshot {i}/{n}…", 0.60 + 0.35 * (i / max(1, n))
                ),
            )

    if move_video:
        _report("Moving video file…", 0.97)
        ext = os.path.splitext(video_path)[1]
        dest_video = os.path.join(folder, f"{release_name}{ext}")
        if os.path.abspath(video_path) != os.path.abspath(dest_video):
            shutil.move(video_path, dest_video)
        final_video = dest_video
    else:
        final_video = video_path

    _report("Done", 1.0)

    return {
        "folder":  folder,
        "nfo":     nfo_path,
        "video":   final_video,
        "sample":  sample_path,
        "subs":    sub_paths,
        "screens": screen_paths,
    }


# =============================================================================
# 11b. FFMPEG-BASED EXTRACTION (Sample / Subtitles / Screenshots)
# =============================================================================
# Release-folder side effects: when the user enables `with_subfolders` for
# the wrapper folder, we can also populate Sample/, Subs/, Screens/ with
# actual content using ffmpeg. The binary is provided by `imageio-ffmpeg`
# so we don't need a system install. If imageio-ffmpeg is missing, the
# extractors fall back to a system `ffmpeg` on $PATH; if both are missing,
# extraction is silently skipped and only the empty folders remain.


def get_ffmpeg_executable() -> Optional[str]:
    """
    Return a usable path to a ffmpeg binary, or None.

    Strategy:
    1. `imageio-ffmpeg` bundled binary (preferred — fully self-contained).
    2. `ffmpeg` on the system PATH (fallback for users who already have it).
    """
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    return shutil.which("ffmpeg")


def get_video_duration_seconds(meta: dict) -> Optional[float]:
    """
    Best-effort numeric duration in seconds, parsed back from the formatted
    `meta["duration"]` string. Used to pick a random sample offset.
    """
    text = (meta or {}).get("duration", "")
    if not text or text == "N/A":
        return None
    # "1 h 49 m 48 s" → 1*3600 + 49*60 + 48 = 6588
    match = re.match(
        r"(?:(?P<h>\d+)\s*h\s*)?(?P<m>\d+)\s*m\s*(?P<s>\d+)\s*s",
        text.strip(),
    )
    if not match:
        return None
    parts = match.groupdict(default="0")
    return int(parts["h"]) * 3600 + int(parts["m"]) * 60 + int(parts["s"])


def format_timecode(seconds: float, *, for_filename: bool = False) -> str:
    """
    Format a time offset as a timecode.

    `for_filename=False` → "HH:MM:SS" (for display / burn-in).
    `for_filename=True`  → "HhMMmSSs" (filename-safe: no colons).
    """
    total = int(max(0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if for_filename:
        return f"{hours:d}h{minutes:02d}m{secs:02d}s"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# Common system font locations across platforms, used to burn a timecode
# onto screenshots via ffmpeg's drawtext filter. We pick the first that
# exists; if none is found, screenshots are produced without a burned-in
# timecode (the timecode still appears in the filename).
_SYSTEM_FONT_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",        # macOS
    "/System/Library/Fonts/Supplemental/Courier New.ttf",  # macOS
    "/Library/Fonts/Arial.ttf",                            # macOS (legacy)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",     # Debian/Ubuntu
    "/usr/share/fonts/TTF/DejaVuSans.ttf",                 # Arch
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",              # Fedora
    "C:/Windows/Fonts/arial.ttf",                          # Windows
)


def find_system_font() -> Optional[str]:
    """Return the first available system font path, or None."""
    for candidate in _SYSTEM_FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


def build_timecode_drawtext(font_path: Optional[str], offset_seconds: float) -> Optional[str]:
    """
    Build a ffmpeg `drawtext` filter string that prints the timecode in the
    bottom-right corner. Returns None when no font is available.

    Both the font path and the colons in the timecode must be escaped for
    ffmpeg's filtergraph syntax.
    """
    if not font_path:
        return None
    escaped_font = font_path.replace("\\", "/").replace(":", r"\:")
    escaped_text = format_timecode(offset_seconds).replace(":", r"\:")
    return (
        f"drawtext=fontfile='{escaped_font}':text='{escaped_text}'"
        ":x=w-tw-25:y=h-th-25:fontsize=h/24:fontcolor=white"
        ":box=1:boxcolor=black@0.5:boxborderw=12"
    )


def _run_ffmpeg(ffmpeg: str, args: list[str]) -> bool:
    """
    Run ffmpeg with stdout/stderr suppressed. Returns True on success.

    We swallow errors deliberately — extraction is best-effort, and a
    failure shouldn't break the rest of the .nfo workflow.
    """
    # On Windows, subprocess.run() without `creationflags` pops up a
    # console window for every child process — very noticeable when we
    # spawn ffmpeg dozens of times for screenshots. CREATE_NO_WINDOW
    # (= 0x08000000) suppresses that. No-op on macOS / Linux.
    extra: dict = {}
    if sys.platform == "win32":
        extra["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            **extra,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def extract_sample(video_path: str,
                   output_dir: str,
                   *,
                   duration_seconds: int = 60,
                   meta: Optional[dict] = None) -> Optional[str]:
    """
    Cut a `duration_seconds` sample from a random middle point of the video.

    The stream is copied (no re-encode) so the sample is produced almost
    instantly regardless of file size. Returns the path of the created
    sample file, or None on failure.
    """
    ffmpeg = get_ffmpeg_executable()
    if not ffmpeg:
        return None

    total = get_video_duration_seconds(meta or {}) or 0
    # Stay clear of the head/tail credits — pick within the middle 60 % of
    # the file. If we don't know the duration, start at 60 s.
    if total > duration_seconds * 3:
        margin = max(30.0, total * 0.2)
        start = random.uniform(margin, max(margin, total - margin - duration_seconds))
    else:
        start = 60.0 if total > 120 else 0.0

    ext = os.path.splitext(video_path)[1] or ".mkv"
    timecode = format_timecode(start, for_filename=True)
    sample_name = f"{release_name_from_video(video_path)}-sample_{timecode}{ext}"
    sample_path = os.path.join(output_dir, sample_name)

    ok = _run_ffmpeg(ffmpeg, [
        "-ss", f"{start:.2f}",
        "-i", video_path,
        "-t", str(duration_seconds),
        # `-map 0` copies EVERY stream (all audio + all subtitle tracks),
        # not just the default ones ffmpeg would otherwise pick.
        "-map", "0",
        # Drop the source chapters: a 60 s clip shouldn't carry the full
        # film's chapter markers (they'd all be out of range / "incomplete").
        "-map_chapters", "-1",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        sample_path,
    ])
    return sample_path if ok and os.path.isfile(sample_path) else None


def extract_subtitles(video_path: str, output_dir: str) -> list[str]:
    """
    Extract every subtitle track from the source media into `output_dir`.

    Each track is named `<release>.<index>.<lang>.<ext>`. The extension is
    chosen based on the track's MediaInfo `format` field. Returns the list
    of files actually written.
    """
    ffmpeg = get_ffmpeg_executable()
    if not ffmpeg:
        return []

    try:
        media = parse_media(video_path)
    except LibMediaInfoNotFound:
        return []
    sub_tracks = [t for t in media.tracks if t.track_type == "Text"]
    if not sub_tracks:
        return []

    # Map MediaInfo's `Format` to a file extension. Anything unknown is left
    # as-is and ffmpeg will pick a sensible container.
    fmt_to_ext = {
        "UTF-8": "srt", "SRT": "srt",
        "ASS": "ass", "SSA": "ssa",
        "PGS": "sup", "VobSub": "idx",
    }

    release = release_name_from_video(video_path)
    written: list[str] = []
    for idx, track in enumerate(sub_tracks):
        lang_code = str(field(track, "language", default="") or "und")
        fmt = str(field(track, "format", default="") or "")
        ext = fmt_to_ext.get(fmt.upper(), fmt.lower() if fmt.isalpha() else "srt")

        out_name = f"{release}.{idx + 1}.{lang_code}.{ext}"
        out_path = os.path.join(output_dir, out_name)

        # `-map 0:s:<idx>` selects the Nth subtitle stream; `-c copy` keeps
        # the original codec (essential for PGS / VobSub which can't be
        # transcoded to SRT without OCR).
        ok = _run_ffmpeg(ffmpeg, [
            "-i", video_path,
            "-map", f"0:s:{idx}",
            "-c", "copy",
            out_path,
        ])
        if ok and os.path.isfile(out_path):
            written.append(out_path)
    return written


def generate_screenshots(video_path: str,
                         output_dir: str,
                         *,
                         count: int = 5,
                         meta: Optional[dict] = None,
                         burn_timecode: bool = True,
                         progress_callback: Optional[Callable[[int, int], None]] = None) -> list[str]:
    """
    Grab `count` screenshots spaced evenly across the middle 80 % of the file.

    Each screenshot's timecode is encoded in its filename
    (`screen_01_0h45m30s.png`) and, when a system font is available, also
    burned into the bottom-right corner of the image via drawtext. If the
    bundled ffmpeg lacks drawtext, we transparently retry without the
    overlay so a screenshot is still produced.

    `progress_callback(i, count)` is called after each screenshot.
    Returns the list of files actually written.
    """
    if count < 1:
        return []
    ffmpeg = get_ffmpeg_executable()
    if not ffmpeg:
        return []

    total = get_video_duration_seconds(meta or {})
    if not total or total <= 0:
        # Without a known duration we can't space screenshots out; use rough
        # fixed offsets so we still produce something.
        offsets = [60.0 + i * 60.0 for i in range(count)]
    else:
        # Skip the first/last 10 % to avoid intro/credits frames.
        head = total * 0.10
        tail = total * 0.90
        span = tail - head
        offsets = [head + span * (i + 1) / (count + 1) for i in range(count)]

    font = find_system_font() if burn_timecode else None
    digits = max(2, len(str(count)))
    written: list[str] = []
    for i, offset in enumerate(offsets, start=1):
        tc_file = format_timecode(offset, for_filename=True)
        out_path = os.path.join(output_dir, f"screen_{i:0{digits}d}_{tc_file}.png")
        base_args = ["-ss", f"{offset:.2f}", "-i", video_path, "-frames:v", "1"]

        drawtext = build_timecode_drawtext(font, offset)
        args = base_args + (["-vf", drawtext] if drawtext else []) + ["-q:v", "2", out_path]
        ok = _run_ffmpeg(ffmpeg, args)
        if not ok and drawtext:
            # drawtext unavailable in this ffmpeg build → retry without it.
            ok = _run_ffmpeg(ffmpeg, base_args + ["-q:v", "2", out_path])

        if ok and os.path.isfile(out_path):
            written.append(out_path)
        if progress_callback:
            try:
                progress_callback(i, count)
            except Exception:  # noqa: BLE001
                pass
    return written


# =============================================================================
# 12. CLI WORKFLOW
# =============================================================================

def clean_dragdrop_path(raw: str) -> str:
    r"""
    macOS Terminal drag-and-drop produces backslash-escaped paths like
    `/Users/.../My\ Video.mkv`. Strip the escapes (and any surrounding quotes).
    """
    return raw.strip().strip("'\"").replace("\\", "")


def prompt_yes_no(question: str, *, default: bool = False) -> bool:
    """Yes/no prompt that returns True on 'y'/'Y' and honours a default on Enter."""
    suffix = " (Y/n): " if default else " (y/N): "
    raw = input(question + suffix).strip().lower()
    if not raw:
        return default
    return raw == "y"


def prompt_video_path() -> str:
    """Loop until the user provides a path to an existing file or folder."""
    while True:
        raw = input("Enter the path to the video file (or a folder for a season): ")
        path = clean_dragdrop_path(raw)
        if not path:
            print("  → Empty path, try again.")
            continue
        if os.path.isdir(path):
            return path
        if not os.path.isfile(path):
            print(f"  → Not found: {path}")
            continue
        ext = Path(path).suffix.lower()
        if ext and ext not in VIDEO_EXTENSIONS:
            if not prompt_yes_no(f"  → '{ext}' is not a known video extension. Continue anyway?"):
                continue
        return path


def prompt_choice(label: str, choices: tuple) -> str:
    """Prompt the user to pick one of `choices` (returns the chosen string)."""
    print(f"{label}:")
    for i, choice in enumerate(choices, start=1):
        print(f"  {i}. {choice}")
    while True:
        raw = input("Enter the number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print("  → Invalid choice. Try again.")


def prompt_release_date(default_date: str) -> str:
    """Ask whether to use today's date, the file's mtime, or a custom value."""
    if prompt_yes_no("Was the file released today?"):
        return datetime.now().strftime("%d-%m-%Y")
    if default_date != "N/A":
        print(f"Detected file modification date: {default_date}")
        if prompt_yes_no("Do you want to use this date?", default=True):
            return default_date
    while True:
        custom = input("Enter the release date (DD-MM-YYYY): ").strip()
        if is_valid_release_date(custom):
            return custom
        print("  → Invalid date format. Use DD-MM-YYYY.")


def prompt_source() -> str:
    """Pick from the curated list, or enter a custom source name."""
    print("Select the source:")
    for i, src in enumerate(DEFAULT_SOURCES, start=1):
        print(f"  {i}. {src}")
    while True:
        choice = input("Enter the number corresponding to the source: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(DEFAULT_SOURCES):
            picked = DEFAULT_SOURCES[int(choice) - 1]
            if picked == "Other":
                return input("Enter the source name: ").strip() or "Other"
            return picked
        print("  → Invalid choice. Try again.")


def prompt_hardcoded_subs() -> list[dict]:
    """If the user reports hardcoded subs, collect language + type."""
    if not prompt_yes_no("No subtitles detected. Are they hardcoded?"):
        return []
    lang = input("  Language: ").strip() or "Unknown"
    s_type = input("  Type (Full / SDH / Forced): ").strip() or "Full"
    return [{"language": lang, "type": s_type}]


def prompt_note(hdr_format: Optional[str] = None) -> str:
    """
    Optional custom note for the bottom banner.

    When the file is HDR, we offer the user a pre-built compatibility
    warning as the default — they can accept it as-is, edit it, or type
    something completely different.
    """
    suggested = build_hdr_warning_note(hdr_format)
    if suggested:
        print("HDR detected. Suggested note:")
        print(f"  {suggested}")
        if prompt_yes_no("Use this HDR warning as the note?", default=True):
            return suggested

    if prompt_yes_no("Do you want to write a custom note?"):
        return input("Enter your note: ").strip() or DEFAULT_NOTE
    return DEFAULT_NOTE


def prompt_links() -> Optional[dict]:
    """Optional LiNKS section input (TMDB / TVDB / iMDB)."""
    if not prompt_yes_no("Add a LiNKS section (TMDB / iMDB)?"):
        return None
    links: dict[str, str] = {}
    for label in DEFAULT_LINK_LABELS:
        url = input(f"  {label} ").strip()
        if url:
            links[label] = url
    return links or None


def prompt_banner_text() -> tuple[Optional[str], Optional[str]]:
    """Optional custom header / footer text (rendered via pyfiglet)."""
    if not prompt_yes_no("Customize the header / footer ASCII art?"):
        return None, None
    header = input("  Header text (blank = keep default 'NOGROUP'): ").strip() or None
    footer = input("  Footer text (blank = keep default 'NOGROUP'): ").strip() or None
    return header, footer


def prompt_folder_options() -> dict:
    """Optional wrapper-folder generation around the produced .nfo."""
    if not prompt_yes_no("Create a wrapper folder for this release?"):
        return {"create": False}

    with_subfolders = prompt_yes_no(
        "  Include subfolders (Sample / Subs / Screens)?"
    )
    extract_sample_flag = False
    extract_subs_flag = False
    generate_screens_flag = False
    sample_duration = 60
    screens_count = 5

    if with_subfolders:
        extract_sample_flag = prompt_yes_no(
            "    Extract a random sample clip into Sample/?"
        )
        if extract_sample_flag:
            raw = input("      Sample duration in seconds [60]: ").strip()
            try:
                sample_duration = int(raw) if raw else 60
            except ValueError:
                sample_duration = 60
        extract_subs_flag = prompt_yes_no(
            "    Extract embedded subtitles into Subs/?"
        )
        generate_screens_flag = prompt_yes_no(
            "    Generate screenshots into Screens/?"
        )
        if generate_screens_flag:
            raw = input("      How many screenshots? [5]: ").strip()
            try:
                screens_count = int(raw) if raw else 5
            except ValueError:
                screens_count = 5

    return {
        "create":                True,
        "with_subfolders":       with_subfolders,
        "move_video":            prompt_yes_no("  Move the video file into the folder?"),
        "extract_sample_flag":   extract_sample_flag,
        "sample_duration":       sample_duration,
        "extract_subs_flag":     extract_subs_flag,
        "generate_screens_flag": generate_screens_flag,
        "screens_count":         screens_count,
    }


def run_cli(initial_path: Optional[str] = None) -> int:
    """Interactive CLI workflow. Returns a shell-style exit code."""
    try:
        video_path = initial_path or prompt_video_path()
        if initial_path and not (os.path.isfile(initial_path) or os.path.isdir(initial_path)):
            print(f"Error: not found: {initial_path}", file=sys.stderr)
            return 1

        is_folder = os.path.isdir(video_path)
        if is_folder:
            print(f"Folder mode: {len(list_video_files(video_path))} video file(s) found.")
            duration_mode = prompt_choice("How to combine durations", FOLDER_DURATION_MODES)
            bitrate_mode = prompt_choice("How to combine bitrates", FOLDER_BITRATE_MODES)
            print("Reading metadata…")
            meta = collect_metadata(
                video_path, bitrate_mode=bitrate_mode, duration_mode=duration_mode,
            )
        else:
            print("Reading metadata…")
            meta = collect_metadata(video_path)
        print(
            f"  → {len(meta['audios'])} audio track(s), "
            f"{len(meta['subtitles'])} subtitle track(s)"
        )

        release_date     = prompt_release_date(meta["file_modified_date"])
        source           = prompt_source()
        hardcoded        = prompt_hardcoded_subs() if not meta["subtitles"] else []
        note             = prompt_note(meta.get("hdr_format"))
        links            = prompt_links()
        header_t, footer_t = prompt_banner_text()
        raw = (get_raw_mediainfo_for_path(video_path)
               if prompt_yes_no("Append the raw MediaInfo dump at the end?")
               else None)

        nfo_content = build_nfo(
            video_path, meta,
            release_date=release_date,
            source=source,
            hardcoded_subs=hardcoded,
            note=note,
            links=links,
            header_text=header_t,
            footer_text=footer_t,
            raw_mediainfo=raw,
        )

        # Folder mode: write the .nfo straight into the folder (the
        # wrapper-folder / sample / screenshot options are per-file only).
        if is_folder:
            release_name = os.path.basename(os.path.normpath(video_path))
            nfo_path = os.path.join(video_path, release_name + ".nfo")
            with open(nfo_path, "w", encoding="utf-8") as fh:
                fh.write(nfo_content)
            print(f"NFO file generated at: {nfo_path}")
            return 0

        folder_opts = prompt_folder_options()
        if folder_opts["create"]:
            result = generate_release_folder(
                video_path, nfo_content,
                with_subfolders=folder_opts["with_subfolders"],
                move_video=folder_opts["move_video"],
                extract_sample_flag=folder_opts["extract_sample_flag"],
                sample_duration=folder_opts["sample_duration"],
                extract_subs_flag=folder_opts["extract_subs_flag"],
                generate_screens_flag=folder_opts["generate_screens_flag"],
                screens_count=folder_opts["screens_count"],
                meta=meta,
            )
            print(f"Release folder created: {result['folder']}")
            print(f"  NFO   → {result['nfo']}")
            print(f"  Video → {result['video']}"
                  + ("  (moved)" if folder_opts["move_video"] else "  (left in place)"))
            if result["sample"]:
                print(f"  Sample → {result['sample']}")
            if result["subs"]:
                print(f"  Subs   → {len(result['subs'])} extracted")
            if result["screens"]:
                print(f"  Screens → {len(result['screens'])} generated")
        else:
            nfo_path = os.path.join(
                os.path.dirname(video_path),
                release_name_from_video(video_path) + ".nfo",
            )
            with open(nfo_path, "w", encoding="utf-8") as fh:
                fh.write(nfo_content)
            print(f"NFO file generated at: {nfo_path}")
        return 0

    except LibMediaInfoNotFound as exc:
        print(f"\n[!] {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"\n[!] File not found: {exc.filename}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"\n[!] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.")
        return 130


# =============================================================================
# 13. ENTRY POINT
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="NFO Generator — stylized .nfo files for video releases.",
    )
    parser.add_argument(
        "path", nargs="?",
        help="Optional video file path (skips the picker prompt).",
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="Force CLI mode (skip the GUI).",
    )
    parser.add_argument(
        "--gui", action="store_true",
        help="Force GUI mode (even when a path is given on the command line).",
    )
    args = parser.parse_args()

    use_gui = args.gui or (not args.cli and not args.path)
    if use_gui:
        try:
            from nfo_gui import launch_gui
        except ImportError as exc:
            print(f"[!] GUI module not available ({exc}). Falling back to CLI.")
            print("    Install GUI dependencies with: pip install -r requirements.txt")
            return run_cli(initial_path=args.path)
        return launch_gui(initial_path=args.path)

    return run_cli(initial_path=args.path)

