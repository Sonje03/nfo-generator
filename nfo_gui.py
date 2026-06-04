"""
nfo_gui
=======

CustomTkinter + tkinterdnd2 GUI front-end for NFO_generator.

The window is organised into four tabs (Release / Content / Style / Output)
with a fixed footer hosting the Generate button + status + progress + result.
Business logic lives entirely in NFO_generator.py; this module is the view.

Layout
------
    ┌───────────────────────────────────────────────────────┐
    │ NFO Generator              [Light │ Dark │ System]   │
    ├───────────────────────────────────────────────────────┤
    │ [Release │ Content │ Style │ Output]                  │
    │                                                       │
    │   ...active tab content (scrollable)...               │
    │                                                       │
    ├───────────────────────────────────────────────────────┤
    │ Status:  ✓ ...                                        │
    │ [████████░░░░] (progress, only while busy)            │
    │ [           Generate NFO                ]             │
    │ [ Result text ...                       ]             │
    └───────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

# =============================================================================
# Theme bootstrap — MUST run before any CTk widget is created. CustomTkinter
# locks in the active theme the first time a `CTk` window is constructed,
# so calling `set_default_color_theme` inside `NFOApp.__init__` (after
# `super().__init__()`) is too late: the root would already be painted with
# the default "blue" theme.
# =============================================================================
import json as _json  # noqa: E402
import tempfile as _tempfile  # noqa: E402
from pathlib import Path as _Path  # noqa: E402


# Font choices offered in the Style tab. Each entry is (display name,
# family, default_size). The size is per-font because a pixel / display
# face at 13px looks oversized next to a proportional sans at the same
# point size — the wider glyph cells need a smaller point size to keep
# the overall UI density consistent.
#
# All non-system fonts are dropped into ``assets/fonts/`` and registered
# at the process level on startup (see ``_load_local_fonts``); see
# ``assets/fonts/README.md`` for download links.
_AVAILABLE_FONT_FAMILIES: tuple[tuple[str, str, int], ...] = (
    # The retro NFO look — default. Pixel font matching the icon.
    ("Press Start 2P (pixel, default)", "Press Start 2P",  9),
    # Modern, neutral sans — most readable fallback.
    ("Aptos (modern sans)",             "Aptos",          13),
    # Distinctive monospace / display alternatives that still fit the
    # retro-NFO vibe without being literal pixel fonts.
    # Sizes are tuned so each face reads at a similar perceptual weight
    # — taller / narrower faces (VT323, Share Tech Mono) need a few
    # more points than chunkier ones.
    ("VT323 (CRT terminal)",            "VT323",          20),
    ("Silkscreen (compact pixel)",      "Silkscreen",     10),
    ("Pixelify Sans (proportional pixel)", "Pixelify Sans", 13),
    ("Major Mono Display (uppercase)",  "Major Mono Display", 11),
    ("Share Tech Mono (futuristic)",    "Share Tech Mono", 15),
    ("JetBrains Mono (dev mono)",       "JetBrains Mono",  12),
    # System fallbacks, kept for users who don't want bundled fonts.
    ("SF Pro Text (macOS native)",      "SF Pro Text",    13),
    ("Segoe UI (Windows native)",       "Segoe UI",       13),
    ("System monospace",                "Menlo" if sys.platform == "darwin"
                                         else "Consolas" if sys.platform == "win32"
                                         else "DejaVu Sans Mono",
                                                            12),
)


def _register_font_file(ttf_path: _Path) -> bool:
    """
    Register a `.ttf` file with the OS for the current *process only*.

    Uses platform-native APIs through ctypes so no extra dependency is needed
    and the system font collection is left untouched (the font disappears
    again when the GUI quits — exactly what you want for a "drop it in,
    try it out" workflow).

    Returns True on success, False otherwise. Failure is silent: a missing
    font just means Tk falls back to its default sans-serif when the family
    name is later requested.
    """
    try:
        if sys.platform == "darwin":
            # CoreText: CTFontManagerRegisterFontsForURL with process scope.
            import ctypes
            from ctypes import c_void_p, c_int, c_uint32

            cf = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            ct = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreText.framework/CoreText"
            )
            cf.CFURLCreateFromFileSystemRepresentation.restype = c_void_p
            cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
                c_void_p, ctypes.c_char_p, c_int, c_int,
            ]
            ct.CTFontManagerRegisterFontsForURL.restype = c_int
            ct.CTFontManagerRegisterFontsForURL.argtypes = [
                c_void_p, c_uint32, c_void_p,
            ]
            path_bytes = str(ttf_path).encode("utf-8")
            url = cf.CFURLCreateFromFileSystemRepresentation(
                None, path_bytes, len(path_bytes), 0,
            )
            if not url:
                return False
            # kCTFontManagerScopeProcess = 1
            return bool(ct.CTFontManagerRegisterFontsForURL(url, 1, None))

        elif sys.platform == "win32":
            # GDI: AddFontResourceExW with FR_PRIVATE so it's per-process.
            import ctypes
            FR_PRIVATE = 0x10
            return bool(ctypes.windll.gdi32.AddFontResourceExW(
                str(ttf_path), FR_PRIVATE, 0,
            ))

        else:  # Linux / *BSD: fontconfig
            import ctypes
            fc = ctypes.cdll.LoadLibrary("libfontconfig.so.1")
            fc.FcConfigAppFontAddFile.restype = ctypes.c_int
            fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            # Passing NULL for the config means "current default config".
            return bool(fc.FcConfigAppFontAddFile(None, str(ttf_path).encode("utf-8")))

    except Exception:  # noqa: BLE001
        return False


def _load_local_fonts() -> list[str]:
    """
    Register every ``*.ttf`` / ``*.otf`` in ``assets/fonts/`` with the OS for
    this process. Returns the list of absolute paths successfully loaded.

    Called before ``_bootstrap_theme`` so the freshly-registered families
    are available the moment CustomTkinter starts measuring widgets.
    """
    fonts_dir = _Path(__file__).resolve().parent / "assets" / "fonts"
    if not fonts_dir.is_dir():
        return []
    loaded: list[str] = []
    for ttf in sorted(fonts_dir.glob("*.[oOtT][tT][fF]")):
        if _register_font_file(ttf):
            loaded.append(str(ttf))
    if loaded:
        # Helpful one-liner in the console — silent for users who never
        # look at stdout, useful for anyone troubleshooting font issues.
        print(f"[nfo_gui] Registered {len(loaded)} local font(s) from assets/fonts/.")
    return loaded


# Must run before any CTk widget is constructed (same reason as the theme).
_load_local_fonts()


def _bootstrap_theme() -> None:
    """
    Load the custom theme JSON, optionally swap the CTkFont family based on
    the user's saved preference, then register the (possibly modified) theme
    with CustomTkinter.

    A temp file is used when the font family is overridden so the original
    JSON on disk stays unmodified — keeps the repo clean and gives every
    launch a fresh chance to honour the latest user config.

    Diagnostic prints go to stdout so PyCharm's Run console makes it
    obvious why a font might not appear (config missing, family typo,
    Tk silently falling back, …).
    """
    theme_path = _Path(__file__).resolve().parent / "assets" / "theme.json"

    # Pull the saved preferences. Reading the config here is safe — the
    # import is at module level, before any GUI code runs.
    saved_family: str | None = None
    saved_mode: str = "Dark"  # default appearance if nothing's been saved yet
    try:
        from nfo_generator import load_user_config as _load_cfg
        _cfg = _load_cfg()
        saved_family = (_cfg.get("font_family") or "").strip() or None
        _mode = (_cfg.get("appearance_mode") or "").strip()
        if _mode in ("Light", "Dark", "System"):
            saved_mode = _mode
    except Exception:  # noqa: BLE001
        saved_family = None  # config corruption shouldn't block the launch

    if theme_path.exists():
        try:
            theme = _json.loads(theme_path.read_text(encoding="utf-8"))
            # CustomTkinter iterates over every top-level key and calls
            # `.keys()` on the value, so any stray non-dict entry (such as
            # an `_comment: "..."` annotation) crashes loading with
            # `'str' object has no attribute 'keys'`. Strip them defensively.
            theme = {k: v for k, v in theme.items() if isinstance(v, dict)}
            if saved_family and "CTkFont" in theme:
                # Pick the matching size from our table, falling back to the
                # theme's default if the saved family isn't in our picker.
                size_lookup = {fam: size for (_, fam, size) in _AVAILABLE_FONT_FAMILIES}
                size_override = size_lookup.get(saved_family)
                for plat in ("macOS", "Windows", "Linux"):
                    if plat in theme["CTkFont"]:
                        theme["CTkFont"][plat]["family"] = saved_family
                        if size_override is not None:
                            theme["CTkFont"][plat]["size"] = size_override
            # Write a temp theme file so CTk reads the (possibly cleaned-up
            # or font-overridden) values. We always go through the temp file
            # path now because the strip-comments pass above produces a
            # different dict than what's on disk.
            tmp = _tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8",
            )
            _json.dump(theme, tmp)
            tmp.close()
            ctk.set_default_color_theme(tmp.name)
        except Exception as exc:  # noqa: BLE001
            print(f"[nfo_gui] Custom theme failed to load: {exc!r}; using 'blue'.")
            ctk.set_default_color_theme("blue")
    else:
        ctk.set_default_color_theme("blue")
    ctk.set_appearance_mode(saved_mode)


_bootstrap_theme()


from nfo_generator import (
    __version__ as NFO_GENERATOR_VERSION,
    check_for_update_async,
    DEFAULT_LINK_LABELS,
    DEFAULT_NOTE,
    DEFAULT_PYFIGLET_FONT,
    DEFAULT_SOURCES,
    anilist_lookup,
    anilist_search_candidates,
    download_image_bytes,
    LibMediaInfoNotFound,
    PYFIGLET_FONT_CHOICES,
    TMDBError,
    VIDEO_EXTENSIONS,
    build_hdr_warning_note,
    build_nfo,
    clean_dragdrop_path,
    collect_metadata,
    generate_release_folder,
    get_raw_mediainfo_for_path,
    guess_season_episode,
    is_valid_release_date,
    load_user_config,
    logger,
    release_name_from_video,
    resolve_title_year_from_path,
    save_user_config,
    tmdb_lookup,
    tmdb_search_candidates,
    tvdb_lookup,
    tvdb_search_candidates,
)


# =============================================================================
# Hybrid root window: customtkinter `CTk` + tkinterdnd2's `DnDWrapper`.
# =============================================================================

class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper):
    """A customtkinter root window that also accepts native file drops."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


# Thumbnail size for the inline TMDB candidate picker.
_CANDIDATE_THUMB_SIZE = (60, 90)


# =============================================================================
# Tooltip helper (CustomTkinter has no native one)
# =============================================================================
# Shows a small frame near the hovered widget after a short delay. Plain Tk
# Toplevel + a CTkLabel inside, so it picks up the current theme. The widget
# can be any tk/CTk widget — we just bind <Enter>/<Leave>.

class _Tooltip:
    """Lazy hover tooltip for any widget."""

    def __init__(self, widget, text: str, *, delay_ms: int = 500,
                 wraplength: int = 320) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>",       self._on_enter, add="+")
        widget.bind("<Leave>",       self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _event) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _event=None) -> None:
        self._cancel()
        self._hide()

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:  # noqa: BLE001
                pass
            self._after_id = None

    def _show(self) -> None:
        import tkinter as tk
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip = tk.Toplevel(self.widget)
        tip.overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
            tip.geometry(f"+{x}+{y}")
        except Exception:  # noqa: BLE001
            pass
        frame = ctk.CTkFrame(tip, corner_radius=6, border_width=1)
        frame.pack()
        ctk.CTkLabel(
            frame, text=self.text, justify="left",
            font=ctk.CTkFont(size=11), wraplength=self.wraplength,
        ).pack(padx=8, pady=4)
        self._tip = tip

    def _hide(self) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._tip = None


# =============================================================================
# Main application window
# =============================================================================

class NFOApp(CTkDnD):
    """The NFO Generator main window."""

    WINDOW_WIDTH = 780
    WINDOW_HEIGHT = 940
    MIN_WIDTH = 720
    MIN_HEIGHT = 760

    def __init__(self, initial_path: str | None = None) -> None:
        # NOTE: the appearance mode + colour theme were already applied at
        # module-import time (see the "Theme bootstrap" block at the top of
        # this file). Doing it here would be too late: super().__init__()
        # creates the Tk root, which freezes the active theme.
        super().__init__()

        self.title("NFO Generator")
        self.geometry(f"{self.WINDOW_WIDTH}x{self.WINDOW_HEIGHT}")
        self.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)

        # Window / dock icon (best-effort; safe to skip if the file is missing
        # or the platform's Tk doesn't accept PNG iconphoto).
        self._apply_window_icon()

        # --- State ----------------------------------------------------------
        self.video_path: str | None = None
        self.metadata: dict | None = None
        self.is_folder: bool = False
        # While False, the note field is auto-managed (HDR warning ↔ empty).
        # Flips True the moment the user types/pastes into it.
        self._note_user_edited: bool = False
        # Persisted user choices (TMDB key, last source, fonts, …).
        self.config = load_user_config()
        # Refs kept alive while the TMDB candidate picker is visible.
        self._candidate_image_refs: list = []

        # --- Build the window ----------------------------------------------
        self._build_ui()
        self._setup_context_menu()
        self._setup_keyboard_shortcuts()
        self._apply_config()
        self._wire_preview_refresh()
        self._setup_validation()

        # Update check — fire-and-forget background thread, banner pops in
        # later only if there's actually a newer release available.
        self._maybe_check_for_updates()

        # File drops anywhere on the window.
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)

        # Persist credentials + provider + UI preferences on FocusOut for
        # the key fields, *and* on window close. Without this, anything the
        # user types but doesn't Generate is lost — surprising for API keys
        # that take effort to paste in.
        for entry in (
            self.tmdb_key_entry,
            self.tvdb_key_entry,
            self.tvdb_pin_entry,
        ):
            entry.bind("<FocusOut>", lambda _e: self._persist_config())
        # Catch the window close (red traffic light on macOS, X on Windows).
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if initial_path:
            self._set_video(initial_path)

    def _on_close(self) -> None:
        """Save the current state and destroy the root window."""
        try:
            self._persist_config()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist config on close: %s", exc)
        self.destroy()

    # ========================================================================
    # UI construction — tabbed layout + fixed footer
    # ========================================================================

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ----------- 0. Top bar -------------------------------------------
        topbar = ctk.CTkFrame(self, fg_color="transparent")
        topbar.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        # Title sits in a sub-frame so the icon hugs the text rather than
        # being pushed right by the expanding `weight=1` column.
        topbar.grid_columnconfigure(1, weight=1)
        title_box = ctk.CTkFrame(topbar, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")

        # Optional icon to the left of the title — silently skipped if
        # Pillow isn't installed or the PNG is missing (the title alone
        # is enough to identify the app).
        icon_path = _Path(__file__).resolve().parent / "assets" / "icon.png"
        if icon_path.exists():
            try:
                from PIL import Image as _PILImage
                pil_icon = _PILImage.open(icon_path)
                ctk_icon = ctk.CTkImage(
                    light_image=pil_icon, dark_image=pil_icon, size=(36, 36),
                )
                # Keep a reference alive (CTk doesn't increment) and grid it.
                self._title_icon_ref = ctk_icon
                ctk.CTkLabel(title_box, text="", image=ctk_icon
                             ).grid(row=0, column=0, padx=(0, 10))
            except Exception as _icon_exc:  # noqa: BLE001
                # PIL missing or PNG corrupt → fall through to the text-only
                # title; never break the launch over a decorative element.
                pass

        ctk.CTkLabel(
            title_box, text="NFO Generator",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=1, sticky="sw")
        # Discreet attribution + version. Sits right of the bold title,
        # baseline-aligned. Uses the dim secondary text color so the
        # eye stays on the main title.
        ctk.CTkLabel(
            title_box,
            text=f"  by Sonje03 — v{NFO_GENERATOR_VERSION}",
            font=ctk.CTkFont(size=10),
            text_color=("gray45", "gray55"),
        ).grid(row=0, column=2, sticky="sw", padx=(2, 0))
        # Appearance mode segmented control. Initial value is wired from
        # the persisted config in `_apply_config()`; default = "Dark".
        self.appearance_var = ctk.StringVar(value="Dark")
        ctk.CTkSegmentedButton(
            topbar, values=["Light", "Dark", "System"],
            variable=self.appearance_var,
            command=self._on_appearance_change,
        ).grid(row=0, column=1, sticky="e")

        # ----------- 1. Tabview --------------------------------------------
        self.tabs = ctk.CTkTabview(self, command=self._on_tab_changed)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        for name in ("Release", "Content", "Style", "Output", "Preview"):
            self.tabs.add(name)

        # Most tabs wrap their content in a scrollable frame so long forms
        # still fit; the Preview tab uses a single textbox that scrolls itself.
        for tab_name, builder, scrollable in (
            ("Release", self._build_release_tab, True),
            ("Content", self._build_content_tab, True),
            ("Style",   self._build_style_tab,   True),
            ("Output",  self._build_output_tab,  True),
            ("Preview", self._build_preview_tab, False),
        ):
            parent = self.tabs.tab(tab_name)
            if scrollable:
                scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
                scroll.pack(fill="both", expand=True)
                scroll.grid_columnconfigure(0, weight=1)
                builder(scroll)
            else:
                parent.grid_columnconfigure(0, weight=1)
                parent.grid_rowconfigure(1, weight=1)
                builder(parent)

        # ----------- 2. Footer (Generate + status + progress + result) -----
        self._build_footer()

    # ------------------------------------------------------------------------
    # Tab: Release
    # ------------------------------------------------------------------------

    def _build_release_tab(self, container) -> None:
        row = 0

        # Drop zone -----------------------------------------------------------
        self.drop_frame = ctk.CTkFrame(
            container, height=130, corner_radius=12,
            # Border matches the theme accent (teal) — keeps the drop zone
            # visually wired to the rest of the app.
            border_width=2, border_color=("#1FA9A1", "#40CCC4"),
        )
        self.drop_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12)); row += 1
        self.drop_frame.grid_propagate(False)
        self.drop_frame.grid_columnconfigure(0, weight=1)
        self.drop_frame.grid_rowconfigure(0, weight=1)

        self.drop_label = ctk.CTkLabel(
            self.drop_frame,
            text="Drop a video file or folder here\nor click to browse",
            font=ctk.CTkFont(size=15, weight="bold"),
            justify="center",
        )
        self.drop_label.grid(row=0, column=0, padx=12, pady=12)
        self.drop_frame.bind("<Button-1>", lambda _: self._browse_video())
        self.drop_label.bind("<Button-1>", lambda _: self._browse_video())

        # Metadata preview ----------------------------------------------------
        self.preview_frame = ctk.CTkFrame(container, corner_radius=10)
        self.preview_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12)); row += 1
        self.preview_frame.grid_columnconfigure(0, weight=1)
        self.preview_label = ctk.CTkLabel(
            self.preview_frame, text="No file loaded",
            anchor="w", justify="left",
            font=ctk.CTkFont(family="Menlo", size=11),
        )
        self.preview_label.grid(row=0, column=0, sticky="ew", padx=12, pady=10)

        # Folder (season) mode (hidden until a folder is loaded) -------------
        self.folder_frame = ctk.CTkFrame(container, corner_radius=10)
        self.folder_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12)); row += 1
        self.folder_frame.grid_columnconfigure(1, weight=1)
        self.folder_frame.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(
            self.folder_frame, text="Folder (season) mode",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 4))
        ctk.CTkLabel(self.folder_frame, text="Duration", anchor="w"
                     ).grid(row=1, column=0, sticky="w", padx=(12, 8))
        self.folder_duration_var = ctk.StringVar(value="total")
        ctk.CTkOptionMenu(
            self.folder_frame, values=["total", "average", "na"],
            variable=self.folder_duration_var,
            command=lambda _=None: self._reparse_folder(),
        ).grid(row=1, column=1, sticky="w", pady=2)
        ctk.CTkLabel(self.folder_frame, text="Bitrate", anchor="w"
                     ).grid(row=1, column=2, sticky="w", padx=(16, 8))
        self.folder_bitrate_var = ctk.StringVar(value="average")
        ctk.CTkOptionMenu(
            self.folder_frame, values=["average", "range", "na"],
            variable=self.folder_bitrate_var,
            command=lambda _=None: self._reparse_folder(),
        ).grid(row=1, column=3, sticky="w", pady=(2, 12))
        self.folder_frame.grid_remove()

        # Source --------------------------------------------------------------
        self._section_label(container, "Source", row); row += 1
        self.source_var = ctk.StringVar(value=DEFAULT_SOURCES[0])
        self.source_menu = ctk.CTkOptionMenu(
            container, values=DEFAULT_SOURCES,
            variable=self.source_var, command=self._on_source_change,
        )
        self.source_menu.grid(row=row, column=0, sticky="ew", pady=(0, 4)); row += 1
        self.source_custom_entry = ctk.CTkEntry(
            container, placeholder_text="Custom source name",
        )
        self.source_custom_entry.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        self.source_custom_entry.grid_remove()
        row += 1

        # Release date --------------------------------------------------------
        self._section_label(container, "Release date", row); row += 1
        date_frame = ctk.CTkFrame(container, fg_color="transparent")
        date_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12)); row += 1
        date_frame.grid_columnconfigure(1, weight=1)
        self.date_mode = ctk.StringVar(value="modified")
        ctk.CTkRadioButton(date_frame, text="Today",
                           variable=self.date_mode, value="today",
                           command=self._on_date_mode_change
                           ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ctk.CTkRadioButton(date_frame, text="File modification date",
                           variable=self.date_mode, value="modified",
                           command=self._on_date_mode_change
                           ).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        ctk.CTkRadioButton(date_frame, text="Custom",
                           variable=self.date_mode, value="custom",
                           command=self._on_date_mode_change
                           ).grid(row=2, column=0, sticky="w", padx=(0, 12))
        self.date_custom_entry = ctk.CTkEntry(
            date_frame, placeholder_text="DD-MM-YYYY", width=140,
        )
        self.date_custom_entry.grid(row=2, column=1, sticky="w")
        self.date_custom_entry.configure(state="disabled")

        # Hardcoded subs (hidden when not applicable) ------------------------
        self.hardcoded_frame = ctk.CTkFrame(container, corner_radius=10)
        self.hardcoded_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12)); row += 1
        self.hardcoded_frame.grid_columnconfigure(1, weight=1)
        self.hardcoded_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self.hardcoded_frame, text="Subtitles are hardcoded",
                        variable=self.hardcoded_var
                        ).grid(row=0, column=0, columnspan=2,
                               sticky="w", padx=12, pady=(8, 4))
        ctk.CTkLabel(self.hardcoded_frame, text="Language", anchor="w"
                     ).grid(row=1, column=0, sticky="w", padx=(12, 8))
        self.hc_lang_entry = ctk.CTkEntry(
            self.hardcoded_frame, placeholder_text="French, English, …",
        )
        self.hc_lang_entry.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=2)
        ctk.CTkLabel(self.hardcoded_frame, text="Type", anchor="w"
                     ).grid(row=2, column=0, sticky="w", padx=(12, 8))
        self.hc_type_var = ctk.StringVar(value="Full")
        ctk.CTkOptionMenu(
            self.hardcoded_frame, values=["Full", "SDH", "Forced"],
            variable=self.hc_type_var,
        ).grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(2, 12))
        self.hardcoded_frame.grid_remove()

        # Note ----------------------------------------------------------------
        self._section_label(container, "Note", row); row += 1
        note_frame, self.note_entry = self._entry_with_paste(
            container, placeholder_text=DEFAULT_NOTE,
        )
        note_frame.grid(row=row, column=0, sticky="ew", pady=(0, 4)); row += 1
        self.note_entry.bind("<Key>", lambda _e: setattr(self, "_note_user_edited", True))
        self.note_entry.bind("<<Paste>>", lambda _e: setattr(self, "_note_user_edited", True))
        self.note_hint = ctk.CTkLabel(
            container, text="", anchor="w", justify="left",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=10, slant="italic"),
        )
        self.note_hint.grid(row=row, column=0, sticky="ew", pady=(0, 4)); row += 1

        # Raw MediaInfo dump checkbox ----------------------------------------
        self.raw_mediainfo_var = ctk.BooleanVar(value=False)
        raw_check = ctk.CTkCheckBox(
            container,
            text="Append raw MediaInfo dump at the end of the .nfo",
            variable=self.raw_mediainfo_var,
        )
        raw_check.grid(row=row, column=0, sticky="w", pady=(0, 12)); row += 1
        _Tooltip(raw_check,
                 "Appends MediaInfo's full text inspection (General / Video / Audio …) "
                 "below the ASCII box. For folder mode, the first episode is used as a "
                 "reference dump.")

    # ------------------------------------------------------------------------
    # Tab: Content (description + TMDB + links)
    # ------------------------------------------------------------------------

    def _build_content_tab(self, container) -> None:
        row = 0
        self._section_label(container, "Description", row); row += 1
        desc_frame = ctk.CTkFrame(container, corner_radius=10)
        desc_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12)); row += 1
        desc_frame.grid_columnconfigure(0, weight=1)

        # Title row (TMDB-overrideable) --------------------------------------
        title_row = ctk.CTkFrame(desc_frame, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        title_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(title_row, text="Title", anchor="w", width=70
                     ).grid(row=0, column=0, sticky="w")
        self.title_entry = ctk.CTkEntry(
            title_row, placeholder_text="shown in the NFO (auto-filled by TMDB)",
        )
        self.title_entry.grid(row=0, column=1, sticky="ew", padx=(4, 6))
        ctk.CTkButton(title_row, text="Paste", width=56,
                      command=lambda: self._paste_into(self.title_entry)
                      ).grid(row=0, column=2)

        # Provider + Language -----------------------------------------------
        provider_row = ctk.CTkFrame(desc_frame, fg_color="transparent")
        provider_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 4))
        provider_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(provider_row, text="Provider", anchor="w", width=70
                     ).grid(row=0, column=0, sticky="w")
        self.provider_var = ctk.StringVar(value="TMDB")
        ctk.CTkOptionMenu(
            provider_row, width=120, variable=self.provider_var,
            values=["TMDB", "AniList", "TVDB"], command=self._on_provider_change,
        ).grid(row=0, column=1, sticky="w")
        self.tmdb_lang_var = ctk.StringVar(value="en-US")
        ctk.CTkOptionMenu(
            provider_row, width=90, variable=self.tmdb_lang_var,
            values=["en-US", "fr-FR", "ja-JP", "es-ES", "de-DE", "it-IT", "pt-BR"],
        ).grid(row=0, column=2, sticky="e")

        # TMDB API key (its own row so we can hide it for non-TMDB) --------
        self.tmdb_key_row = ctk.CTkFrame(desc_frame, fg_color="transparent")
        self.tmdb_key_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))
        self.tmdb_key_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.tmdb_key_row, text="TMDB key", anchor="w", width=70
                     ).grid(row=0, column=0, sticky="w")
        self.tmdb_key_entry = ctk.CTkEntry(
            self.tmdb_key_row,
            placeholder_text="paste your TMDB API key (saved locally)",
        )
        self.tmdb_key_entry.grid(row=0, column=1, sticky="ew", padx=(4, 6))
        # "Clear" button — wipes EVERY saved credential at once (TMDB +
        # TVDB key + PIN) so the user can hand off the app without
        # leaving keys behind.
        ctk.CTkButton(
            self.tmdb_key_row, text="Clear", width=56,
            command=self._clear_api_keys,
        ).grid(row=0, column=2, sticky="e")

        # TVDB API key + optional PIN (hidden unless provider == TVDB) ------
        self.tvdb_key_row = ctk.CTkFrame(desc_frame, fg_color="transparent")
        self.tvdb_key_row.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 4))
        self.tvdb_key_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.tvdb_key_row, text="TVDB key", anchor="w", width=70
                     ).grid(row=0, column=0, sticky="w")
        self.tvdb_key_entry = ctk.CTkEntry(
            self.tvdb_key_row,
            placeholder_text="paste your TVDB v4 API key (saved locally)",
        )
        self.tvdb_key_entry.grid(row=0, column=1, sticky="ew", padx=(4, 6))
        ctk.CTkLabel(self.tvdb_key_row, text="PIN", anchor="e"
                     ).grid(row=0, column=2, sticky="e", padx=(0, 4))
        self.tvdb_pin_entry = ctk.CTkEntry(
            self.tvdb_key_row, width=90,
            placeholder_text="optional",
        )
        self.tvdb_pin_entry.grid(row=0, column=3, sticky="w", padx=(0, 6))
        ctk.CTkButton(
            self.tvdb_key_row, text="Clear", width=56,
            command=self._clear_api_keys,
        ).grid(row=0, column=4, sticky="e")
        self.tvdb_key_row.grid_remove()  # hidden by default (TMDB is the default provider)

        # Fetch row: ID + kind + buttons -------------------------------------
        fetch_row = ctk.CTkFrame(desc_frame, fg_color="transparent")
        fetch_row.grid(row=4, column=0, sticky="ew", padx=12, pady=4)
        fetch_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(fetch_row, text="ID", anchor="w", width=70
                     ).grid(row=0, column=0, sticky="w")
        self.tmdb_id_entry = ctk.CTkEntry(
            fetch_row, placeholder_text="TMDB or AniList ID (e.g. 67043)",
        )
        self.tmdb_id_entry.grid(row=0, column=1, sticky="ew", padx=(4, 6))
        self.tmdb_kind_var = ctk.StringVar(value="auto")
        ctk.CTkOptionMenu(fetch_row, width=90, variable=self.tmdb_kind_var,
                          values=["auto", "movie", "tv"]
                          ).grid(row=0, column=2, padx=(0, 6))
        ctk.CTkButton(fetch_row, text="Fetch by ID", width=90,
                      command=self._fetch_tmdb_by_id).grid(row=0, column=3, padx=(0, 6))
        ctk.CTkButton(fetch_row, text="Auto-search", width=90,
                      command=self._fetch_tmdb_auto).grid(row=0, column=4)

        # Description textbox ------------------------------------------------
        self.desc_textbox = ctk.CTkTextbox(desc_frame, height=90, wrap="word")
        self.desc_textbox.grid(row=5, column=0, sticky="ew", padx=12, pady=4)

        # Paste / Clear / Include / Source row --------------------------------
        desc_btns = ctk.CTkFrame(desc_frame, fg_color="transparent")
        desc_btns.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 4))
        desc_btns.grid_columnconfigure(3, weight=1)
        ctk.CTkButton(desc_btns, text="Paste", width=56,
                      command=self._paste_into_description
                      ).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(desc_btns, text="Clear", width=56,
                      command=lambda: self.desc_textbox.delete("1.0", "end")
                      ).grid(row=0, column=1, padx=(0, 6))
        # Include-other-providers: after a primary fetch, run lookups against
        # the other 2 providers (using their saved credentials) and merge
        # only their owned link rows. Description/title untouched.
        self.include_btn = ctk.CTkButton(
            desc_btns, text="Include other providers", width=170,
            command=self._fetch_include_others,
        )
        self.include_btn.grid(row=0, column=2, padx=(0, 6))
        _Tooltip(
            self.include_btn,
            "Run searches against the other providers using the current Title,\n"
            "and fill any of their link rows (TMDB / iMDB / TVDB / ANiLiST / MAL)\n"
            "that the primary fetch did not provide. Description stays the same.",
        )
        ctk.CTkLabel(desc_btns, text="Source", anchor="e"
                     ).grid(row=0, column=4, sticky="e", padx=(0, 6))
        self.desc_source_var = ctk.StringVar(value="")
        ctk.CTkComboBox(
            desc_btns, width=140, variable=self.desc_source_var,
            values=["", "TMDB", "IMDb", "Crunchyroll", "ADN", "AniList", "Wikipedia"],
        ).grid(row=0, column=5)

        # Status + inline candidate picker -----------------------------------
        self.tmdb_status = ctk.CTkLabel(
            desc_frame, text="", anchor="w",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=10, slant="italic"),
        )
        self.tmdb_status.grid(row=7, column=0, sticky="ew", padx=12, pady=(0, 4))

        self.candidates_frame = ctk.CTkFrame(desc_frame, corner_radius=8)
        self.candidates_frame.grid(row=8, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.candidates_frame.grid_columnconfigure(0, weight=1)
        self.candidates_frame.grid_remove()

        # Links --------------------------------------------------------------
        # Header row groups the "Links (optional)" label with a small Clear
        # action — handy when the previous session's URLs got restored from
        # config and you're starting work on a new title.
        links_header = ctk.CTkFrame(container, fg_color="transparent")
        links_header.grid(row=row, column=0, sticky="ew", pady=(8, 4)); row += 1
        links_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            links_header, text="Links (optional)", anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            links_header, text="Clear", width=56,
            command=self._clear_all_links,
        ).grid(row=0, column=1, sticky="e")

        self.link_entries: dict = {}
        links_frame = ctk.CTkFrame(container, fg_color="transparent")
        links_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12)); row += 1
        links_frame.grid_columnconfigure(1, weight=1)
        for i, label in enumerate(DEFAULT_LINK_LABELS):
            pretty = label.split(".")[0]
            ctk.CTkLabel(links_frame, text=pretty, anchor="w", width=90
                         ).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            link_frame, entry = self._entry_with_paste(
                links_frame, placeholder_text="https://…",
            )
            link_frame.grid(row=i, column=1, sticky="ew", pady=2)
            self.link_entries[label] = entry

    # ------------------------------------------------------------------------
    # Tab: Style (banners)
    # ------------------------------------------------------------------------

    def _build_style_tab(self, container) -> None:
        row = 0

        # UI font picker -----------------------------------------------------
        # Changing the font requires restarting the GUI: CustomTkinter
        # locks the font family the first time a widget is built, so we
        # persist the choice in the config and tell the user to relaunch.
        self._section_label(container, "Interface font", row); row += 1
        font_frame = ctk.CTkFrame(container, fg_color="transparent")
        font_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12)); row += 1
        font_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(font_frame, text="Font", anchor="w", width=90
                     ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        # The dropdown shows pretty labels; we keep the matching family
        # string in self.font_family_var (used at next launch).
        self.font_label_var = ctk.StringVar()
        self.font_family_var = ctk.StringVar(value="Aptos")
        font_labels = [label for (label, _, _) in _AVAILABLE_FONT_FAMILIES]
        self._font_label_to_family = {
            label: family for (label, family, _) in _AVAILABLE_FONT_FAMILIES
        }
        self._font_family_to_label = {
            family: label for (label, family, _) in _AVAILABLE_FONT_FAMILIES
        }
        ctk.CTkOptionMenu(
            font_frame, values=font_labels, variable=self.font_label_var,
            command=self._on_font_choice_changed,
        ).grid(row=0, column=1, sticky="w")
        self.font_hint_label = ctk.CTkLabel(
            font_frame, text="", anchor="w",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=10, slant="italic"),
        )
        self.font_hint_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        # Known cosmetic quirk note. Native macOS / Windows menus are
        # painted by the OS and their hover bar uses font metrics that
        # don't always align with bitmap pixel fonts. Pixelify Sans /
        # VT323 / Major Mono Display have less aggressive metrics if
        # the offset bothers you with Press Start 2P.
        ctk.CTkLabel(
            font_frame,
            text="Tip: dropdown hover bars may look slightly off-center with "
                 "Press Start 2P (a Tk + pixel-font quirk). Pixelify Sans, "
                 "VT323 or Major Mono Display render menus more cleanly.",
            text_color=("gray45", "gray55"),
            font=ctk.CTkFont(size=10, slant="italic"),
            wraplength=600, justify="left", anchor="w",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Banners ------------------------------------------------------------
        self._section_label(container, "ASCII banners (optional)", row); row += 1
        banner_frame = ctk.CTkFrame(container, fg_color="transparent")
        banner_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12)); row += 1
        banner_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(banner_frame, text="Header", anchor="w", width=90
                     ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        header_frame, self.header_entry = self._entry_with_paste(
            banner_frame, placeholder_text="Custom header text (blank = NOGROUP)",
        )
        header_frame.grid(row=0, column=1, sticky="ew", pady=2)

        ctk.CTkLabel(banner_frame, text="Footer", anchor="w", width=90
                     ).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        footer_frame, self.footer_entry = self._entry_with_paste(
            banner_frame, placeholder_text="Custom footer text (blank = none)",
        )
        footer_frame.grid(row=1, column=1, sticky="ew", pady=2)

        # Single Clear button that wipes both Header + Footer in one click.
        # Sits in column 2 so it doesn't fight the Paste buttons that the
        # `_entry_with_paste` helper already provides for each entry.
        ctk.CTkButton(
            banner_frame, text="Clear", width=56,
            command=self._clear_banners,
        ).grid(row=0, column=2, rowspan=2, sticky="ns", padx=(8, 0), pady=2)

        ctk.CTkLabel(banner_frame, text="Font", anchor="w", width=90
                     ).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=2)
        self.banner_font_var = ctk.StringVar(value=DEFAULT_PYFIGLET_FONT)
        ctk.CTkOptionMenu(banner_frame, values=list(PYFIGLET_FONT_CHOICES),
                          variable=self.banner_font_var
                          ).grid(row=2, column=1, sticky="w", pady=2)

    # ------------------------------------------------------------------------
    # Tab: Output (folder generation + extraction options)
    # ------------------------------------------------------------------------

    def _build_output_tab(self, container) -> None:
        row = 0
        self._section_label(container, "Folder generation", row); row += 1
        self.folder_var = ctk.BooleanVar(value=False)
        self.subfolders_var = ctk.BooleanVar(value=False)
        self.move_video_var = ctk.BooleanVar(value=False)
        self.extract_sample_var = ctk.BooleanVar(value=False)
        self.extract_subs_var = ctk.BooleanVar(value=False)
        self.generate_screens_var = ctk.BooleanVar(value=False)
        self.sample_duration_var = ctk.StringVar(value="60")
        self.screens_count_var = ctk.StringVar(value="5")

        ctk.CTkCheckBox(container, text="Create wrapper folder",
                        variable=self.folder_var, command=self._on_folder_toggle
                        ).grid(row=row, column=0, sticky="w", pady=2); row += 1
        self.sub_check = ctk.CTkCheckBox(
            container, text="Include subfolders (Sample / Subs / Screens)",
            variable=self.subfolders_var, command=self._on_subfolders_toggle,
        )
        self.sub_check.grid(row=row, column=0, sticky="w", padx=(24, 0), pady=2); row += 1
        self.move_check = ctk.CTkCheckBox(
            container, text="Move video file into the folder",
            variable=self.move_video_var,
        )
        self.move_check.grid(row=row, column=0, sticky="w", padx=(24, 0), pady=(2, 8)); row += 1

        # Extraction options (only meaningful when subfolders is on).
        ext_frame = ctk.CTkFrame(container, fg_color="transparent")
        ext_frame.grid(row=row, column=0, sticky="ew", padx=(24, 0)); row += 1
        ext_frame.grid_columnconfigure(1, weight=1)

        self.sample_check = ctk.CTkCheckBox(
            ext_frame, text="Extract sample (Sample/)",
            variable=self.extract_sample_var,
        )
        self.sample_check.grid(row=0, column=0, sticky="w", pady=2)
        ctk.CTkLabel(ext_frame, text="Duration (s):", anchor="e"
                     ).grid(row=0, column=1, sticky="e", padx=(8, 4))
        self.sample_duration_entry = ctk.CTkEntry(
            ext_frame, textvariable=self.sample_duration_var, width=60,
        )
        self.sample_duration_entry.grid(row=0, column=2, sticky="w", pady=2)

        self.subs_check = ctk.CTkCheckBox(
            ext_frame, text="Extract subtitles (Subs/)",
            variable=self.extract_subs_var,
        )
        self.subs_check.grid(row=1, column=0, sticky="w", pady=2)

        self.screens_check = ctk.CTkCheckBox(
            ext_frame, text="Generate screenshots (Screens/)",
            variable=self.generate_screens_var,
        )
        self.screens_check.grid(row=2, column=0, sticky="w", pady=2)
        ctk.CTkLabel(ext_frame, text="Count:", anchor="e"
                     ).grid(row=2, column=1, sticky="e", padx=(8, 4))
        self.screens_count_entry = ctk.CTkEntry(
            ext_frame, textvariable=self.screens_count_var, width=60,
        )
        self.screens_count_entry.grid(row=2, column=2, sticky="w", pady=2)

        # Children disabled until the master "wrapper folder" toggle is on.
        self._set_folder_children_state(False)

        # Tooltips on the trickier options (extraction = ffmpeg).
        _Tooltip(self.sample_check,
                 "Stream-copies a short clip from the middle of the video into Sample/. "
                 "All audio/subtitle tracks are kept; the timecode goes into the filename.")
        _Tooltip(self.subs_check,
                 "Extracts every embedded subtitle track into Subs/, in its native format "
                 "(.srt / .ass / .sup / .idx).")
        _Tooltip(self.screens_check,
                 "Saves N screenshots evenly spread across the middle 80 % of the video, "
                 "with the timecode burned in and in the filename.")
        _Tooltip(self.sub_check,
                 "Create Sample / Subs / Screens subfolders next to the .nfo. "
                 "Each one is only populated when its extraction option above is on.")
        _Tooltip(self.move_check,
                 "Move the original video file inside the wrapper folder, alongside the "
                 ".nfo. Otherwise it stays where it is.")

    # ------------------------------------------------------------------------
    # Tab: Preview (live-rendered .nfo)
    # ------------------------------------------------------------------------

    def _build_preview_tab(self, container) -> None:
        # Header: title + Live toggle + Refresh.
        header = ctk.CTkFrame(container, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, anchor="w", text="Live preview of the .nfo",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self.preview_live_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            header, text="Live update", variable=self.preview_live_var,
            command=self._render_preview,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(header, text="Refresh", width=80,
                      command=self._render_preview
                      ).grid(row=0, column=2)

        # The preview textbox itself. wrap=none so the ASCII box stays aligned;
        # the user can scroll horizontally if their window is narrower than 75 chars.
        self.preview_textbox = ctk.CTkTextbox(
            container, wrap="none", activate_scrollbars=True,
            font=ctk.CTkFont(family="Menlo", size=10),
        )
        self.preview_textbox.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.preview_textbox.insert("1.0", "Load a file or folder to see the preview.")
        self.preview_textbox.configure(state="disabled")

    # ------------------------------------------------------------------------
    # Footer (always visible, hosts the main action + feedback)
    # ------------------------------------------------------------------------

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self)
        footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 12))
        footer.grid_columnconfigure(0, weight=1)
        row = 0

        self.status_label = ctk.CTkLabel(
            footer, text="", anchor="w",
            text_color=("gray30", "gray70"),
        )
        self.status_label.grid(row=row, column=0, sticky="ew", padx=8, pady=(8, 2)); row += 1

        self.progress_bar = ctk.CTkProgressBar(footer)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=row, column=0, sticky="ew", padx=8, pady=2)
        self.progress_bar.grid_remove()
        row += 1

        self.generate_btn = ctk.CTkButton(
            footer, text="Generate NFO  (⌘G)", height=40,
            command=self._on_generate, state="disabled",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.generate_btn.grid(row=row, column=0, sticky="ew", padx=8, pady=6); row += 1

        self.result_box = ctk.CTkTextbox(
            footer, height=120, wrap="word", activate_scrollbars=True,
            font=ctk.CTkFont(family="Menlo", size=11),
        )
        self.result_box.grid(row=row, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.result_box.grid_remove()

    @staticmethod
    def _section_label(parent, text: str, row: int) -> None:
        ctk.CTkLabel(parent, text=text, anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).grid(row=row, column=0, sticky="w", pady=(4, 4))

    # ========================================================================
    # Keyboard shortcuts (cross-platform: Cmd on macOS, Ctrl on Win/Linux)
    # ========================================================================

    def _setup_keyboard_shortcuts(self) -> None:
        """
        ⌘G / Ctrl+G  → Generate NFO
        ⌘O / Ctrl+O  → Open file
        ⌘⇧F / Ctrl+Shift+F → TMDB Auto-search
        """
        for combo in ("<Command-g>", "<Control-g>"):
            self.bind_all(combo, self._kb_generate)
        for combo in ("<Command-o>", "<Control-o>"):
            self.bind_all(combo, self._kb_open)
        for combo in ("<Command-Shift-f>", "<Control-Shift-f>",
                      "<Command-Shift-F>", "<Control-Shift-F>"):
            self.bind_all(combo, self._kb_fetch_tmdb)

    def _kb_generate(self, _event=None):
        if str(self.generate_btn.cget("state")) == "normal":
            self._on_generate()
        return "break"

    def _kb_open(self, _event=None):
        self._browse_video()
        return "break"

    def _kb_fetch_tmdb(self, _event=None):
        self._fetch_tmdb_auto()
        return "break"

    # ========================================================================
    # Window icon
    # ========================================================================

    def _apply_window_icon(self) -> None:
        """
        Set the window / dock icon from ``assets/icon.png``.

        Best-effort: silently skipped if the file is missing (e.g. running
        from a stripped-down checkout) or if the platform's Tk build can't
        decode it. PyInstaller-bundled apps get the icon through the
        ``--icon`` flag instead — this method only matters when running
        from source.
        """
        from pathlib import Path
        import tkinter as tk

        candidates = [
            Path(__file__).resolve().parent / "assets" / "icon.png",
            Path.cwd() / "assets" / "icon.png",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                photo = tk.PhotoImage(file=str(path))
                # Keep a ref alive: tk's iconphoto doesn't increment it.
                self._icon_image = photo
                self.iconphoto(True, photo)
                return
            except Exception:  # noqa: BLE001
                continue  # fall through to the next candidate or skip silently

    # ========================================================================
    # Update check
    # ========================================================================

    def _maybe_check_for_updates(self) -> None:
        """
        Kick off the GitHub-Releases update check if the user hasn't
        disabled it. Silent on success / failure / no-update; only when
        a newer release exists does ``_show_update_banner`` run on the
        Tk main loop and paint a one-line notice.
        """
        if not self.config.get("check_updates_on_startup", True):
            return
        repo = self.config.get("github_repo") or "Sonje03/nfo-generator"
        try:
            check_for_update_async(
                repo=repo,
                current_version=NFO_GENERATOR_VERSION,
                on_update=self._show_update_banner,
                ui_thread_dispatch=self.after,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping update check: %s", exc)

    def _show_update_banner(self, latest: str, url: str) -> None:
        """
        Paint a one-line "Update available" banner at the very top of the
        window. Clicking the link opens the release page in the system
        browser. Idempotent: re-calling replaces the banner instead of
        stacking it.
        """
        import webbrowser
        # Remove a previous banner before stacking a new one — happens if
        # the user triggers a forced re-check from a future menu item.
        existing = getattr(self, "_update_banner", None)
        if existing is not None:
            existing.destroy()

        banner = ctk.CTkFrame(self, fg_color=("#1FA9A1", "#40CCC4"), corner_radius=0)
        banner.grid(row=0, column=0, sticky="ew")
        # Push every existing row down by one. Grid stays consistent
        # because we only ever insert at row 0 once per session.
        for child in self.grid_slaves():
            if child is banner:
                continue
            info = child.grid_info()
            if int(info.get("row", 0)) >= 0:
                child.grid(row=int(info["row"]) + 1, column=int(info["column"]))

        ctk.CTkLabel(
            banner,
            text=f"  ✓ Update available — {latest}. Click to open the release page.",
            text_color="#0C0E16",
            anchor="w",
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=10, pady=4)
        ctk.CTkButton(
            banner, text="×", width=24, fg_color="transparent",
            text_color="#0C0E16", hover_color=("#178B85", "#36A9A2"),
            command=banner.destroy,
        ).pack(side="right", padx=(0, 6))

        banner.bind("<Button-1>", lambda _e: webbrowser.open(url))
        for child in banner.winfo_children():
            if not isinstance(child, ctk.CTkButton):
                child.bind("<Button-1>", lambda _e: webbrowser.open(url))

        self._update_banner = banner

    # ========================================================================
    # Clipboard helpers
    # ========================================================================

    def _setup_context_menu(self) -> None:
        """Cross-platform right-click Cut/Copy/Paste/Select-All menu."""
        import tkinter as tk
        self._context_target = None
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Cut",        command=lambda: self._clipboard_action("<<Cut>>"))
        menu.add_command(label="Copy",       command=lambda: self._clipboard_action("<<Copy>>"))
        menu.add_command(label="Paste",      command=lambda: self._clipboard_action("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: self._clipboard_action("<<SelectAll>>"))
        self._context_menu = menu

        def show_menu(event):
            if not isinstance(event.widget, (tk.Entry, tk.Text)):
                return
            self._context_target = event.widget
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        self.bind_all("<Button-2>", show_menu)
        self.bind_all("<Button-3>", show_menu)

    def _clipboard_action(self, virtual_event: str) -> None:
        widget = self._context_target or self.focus_get()
        if widget is not None:
            try:
                widget.event_generate(virtual_event)
            except Exception:  # noqa: BLE001
                pass

    def _entry_with_paste(self, parent, **entry_kwargs):
        """Entry (expanding) + small Paste button. Returns (frame, entry)."""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(frame, **entry_kwargs)
        entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            frame, text="Paste", width=56,
            command=lambda e=entry: self._paste_into(e),
        ).grid(row=0, column=1, padx=(6, 0))
        return frame, entry

    def _paste_into(self, entry) -> None:
        try:
            text = self.clipboard_get()
        except Exception:  # noqa: BLE001
            return
        try:
            entry.delete("sel.first", "sel.last")
        except Exception:  # noqa: BLE001
            pass
        entry.insert("insert", text)
        if entry is getattr(self, "note_entry", None):
            self._note_user_edited = True

    def _paste_into_description(self) -> None:
        try:
            text = self.clipboard_get()
        except Exception:  # noqa: BLE001
            return
        self.desc_textbox.insert("insert", text)

    # ========================================================================
    # Config persistence
    # ========================================================================

    def _apply_config(self) -> None:
        cfg = self.config
        if cfg.get("source") in DEFAULT_SOURCES:
            self.source_var.set(cfg["source"])
        if cfg.get("banner_font") in PYFIGLET_FONT_CHOICES:
            self.banner_font_var.set(cfg["banner_font"])
        if cfg.get("header_text"):
            self.header_entry.insert(0, cfg["header_text"])
        if cfg.get("footer_text"):
            self.footer_entry.insert(0, cfg["footer_text"])
        self.sample_duration_var.set(str(cfg.get("sample_duration", 60)))
        self.screens_count_var.set(str(cfg.get("screens_count", 5)))
        if cfg.get("tmdb_api_key"):
            self.tmdb_key_entry.insert(0, cfg["tmdb_api_key"])
        if cfg.get("tmdb_language"):
            self.tmdb_lang_var.set(cfg["tmdb_language"])
        if cfg.get("tvdb_api_key"):
            self.tvdb_key_entry.insert(0, cfg["tvdb_api_key"])
        if cfg.get("tvdb_pin"):
            self.tvdb_pin_entry.insert(0, cfg["tvdb_pin"])
        if cfg.get("provider") in ("TMDB", "AniList", "TVDB"):
            self.provider_var.set(cfg["provider"])
            self._on_provider_change(cfg["provider"])
        # Font picker — initial label reflects whatever's saved (or Aptos).
        saved_family = cfg.get("font_family") or "Aptos"
        self.font_family_var.set(saved_family)
        self.font_label_var.set(
            self._font_family_to_label.get(saved_family, "Aptos (modern, default)")
        )
        # Appearance mode — sync the segmented control + apply right away
        # (the value set on the var alone wouldn't repaint already-built
        # widgets; ctk.set_appearance_mode does).
        saved_mode = cfg.get("appearance_mode") or "Dark"
        if saved_mode in ("Light", "Dark", "System"):
            self.appearance_var.set(saved_mode)
            ctk.set_appearance_mode(saved_mode)
        for label, url in (cfg.get("links") or {}).items():
            if label in self.link_entries and url:
                self.link_entries[label].insert(0, url)

    def _persist_config(self) -> None:
        self.config.update({
            "source":          self.source_var.get(),
            "banner_font":     self.banner_font_var.get(),
            "header_text":     self.header_entry.get().strip(),
            "footer_text":     self.footer_entry.get().strip(),
            "links":           self._resolve_links() or {},
            "sample_duration": self._resolve_int(self.sample_duration_var, 60),
            "screens_count":   self._resolve_int(self.screens_count_var, 5),
            "tmdb_api_key":    self.tmdb_key_entry.get().strip(),
            "tmdb_language":   self.tmdb_lang_var.get(),
            "tvdb_api_key":    self.tvdb_key_entry.get().strip(),
            "tvdb_pin":        self.tvdb_pin_entry.get().strip(),
            "provider":        self.provider_var.get(),
            "font_family":     self.font_family_var.get(),
            "appearance_mode": self.appearance_var.get(),
        })
        save_user_config(self.config)

    # ========================================================================
    # File selection (browse + drag & drop)
    # ========================================================================

    def _browse_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a video file",
            filetypes=[
                ("Video files", " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS)),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._set_video(path)

    def _on_drop(self, event) -> None:
        raw = event.data.strip()
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        paths = raw.split("} {") if "} {" in event.data else [raw]
        path = clean_dragdrop_path(paths[0])
        if not (os.path.isfile(path) or os.path.isdir(path)):
            messagebox.showerror("Invalid drop", f"Not a file or folder: {path}")
            return
        self._set_video(path)

    def _reparse_folder(self) -> None:
        if self.is_folder and self.video_path:
            self._set_video(self.video_path)

    def _set_video(self, path: str) -> None:
        path = clean_dragdrop_path(path) if "\\" in path else path
        if not (os.path.isfile(path) or os.path.isdir(path)):
            messagebox.showerror("Not found", path)
            return

        self.video_path = path
        self.is_folder = os.path.isdir(path)
        icon = "🗂️" if self.is_folder else "📁"
        self.drop_label.configure(
            text=f"{icon}  {os.path.basename(os.path.normpath(path))}"
        )
        self._set_status("Reading metadata…", busy=True)

        duration_mode = self.folder_duration_var.get()
        bitrate_mode = self.folder_bitrate_var.get()

        def _work() -> None:
            try:
                meta = collect_metadata(
                    path, bitrate_mode=bitrate_mode, duration_mode=duration_mode,
                )
            except LibMediaInfoNotFound as exc:
                self.after(0, lambda: self._on_metadata_error(str(exc)))
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Metadata read failed for %s: %s", path, exc)
                self.after(0, lambda e=exc: self._on_metadata_error(str(e)))
                return
            self.after(0, lambda: self._on_metadata_ready(meta))

        threading.Thread(target=_work, daemon=True).start()

    def _on_metadata_ready(self, meta: dict) -> None:
        self.metadata = meta
        summary = [
            f"Season:     {meta['title']}  ({meta.get('video_count', 1)} files)"
            if meta.get("is_folder")
            else f"Title:      {meta['title']}",
            f"Size:       {meta['file_size']}",
            f"Resolution: {meta['resolution']}  @ {meta['framerate']}  ({meta['aspect_ratio']})",
            f"Codec:      {meta['video_format'] or '—'}   {meta['video_bitrate']}",
            f"Duration:   {meta['duration']}",
            f"Audio:      {len(meta['audios'])} track(s)",
            f"Subtitles:  {len(meta['subtitles'])} track(s)",
            f"HDR:        {meta['hdr_format'] or '—'}",
            f"Modified:   {meta['file_modified_date']}",
        ]
        self.preview_label.configure(text="\n".join(summary))

        # Hardcoded-subs panel only when there are no muxed subs.
        if not meta["subtitles"]:
            self.hardcoded_frame.grid()
        else:
            self.hardcoded_frame.grid_remove()
            self.hardcoded_var.set(False)

        # Pre-fill Title from MediaInfo (TMDB lookup can overwrite later).
        if meta.get("title") and not self.title_entry.get().strip():
            self.title_entry.insert(0, meta["title"])

        # Pre-fill description from MediaInfo when empty (folder mode usually
        # has nothing; movies sometimes do).
        if meta.get("description") and not self.desc_textbox.get("1.0", "end").strip():
            self.desc_textbox.insert("1.0", meta["description"])

        # Auto-manage the HDR warning note unless the user has typed.
        warning = build_hdr_warning_note(meta.get("hdr_format")) or ""
        if not self._note_user_edited:
            self.note_entry.delete(0, "end")
            if warning:
                self.note_entry.insert(0, warning)
            else:
                try:
                    self.note_entry._activate_placeholder()
                except Exception:  # noqa: BLE001
                    pass

        if warning:
            self.note_hint.configure(
                text="HDR detected — note auto-filled with a compatibility warning. Edit freely."
            )
        else:
            self.note_hint.configure(
                text=f"Leave blank to use the default note: {DEFAULT_NOTE}"
            )

        if self.is_folder:
            self.folder_frame.grid()
            self.folder_var.set(False)
            self._set_folder_children_state(False)
        else:
            self.folder_frame.grid_remove()

        self.generate_btn.configure(state="normal")
        self._set_status(f"Loaded {os.path.basename(self.video_path or '')}.")
        self._schedule_preview_refresh()

    def _on_metadata_error(self, message: str) -> None:
        self.metadata = None
        self.preview_label.configure(text="Metadata read failed.")
        self.generate_btn.configure(state="disabled")
        self._set_status("Error.")
        messagebox.showerror("Could not read metadata", message)

    # ========================================================================
    # Form toggle handlers
    # ========================================================================

    def _on_source_change(self, value: str) -> None:
        if value == "Other":
            self.source_custom_entry.grid()
        else:
            self.source_custom_entry.grid_remove()

    def _clear_all_links(self) -> None:
        """Empty every link row in one click — convenient when starting fresh."""
        for entry in self.link_entries.values():
            entry.delete(0, "end")
        # Persist immediately so the cleared state survives a restart even
        # if the user doesn't hit Generate / close before a font / theme
        # change re-saves the full config.
        try:
            self._persist_config()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Couldn't persist cleared links: %s", exc)

    def _clear_api_keys(self) -> None:
        """
        Wipe every stored API credential (TMDB key, TVDB key, TVDB PIN)
        from both the live entries and the persisted config.

        Handy if you're handing the app off to someone else or auditing
        what the project knows about you. The provider rows themselves
        stay visible so you can paste a different key right away.
        """
        for entry in (self.tmdb_key_entry, self.tvdb_key_entry, self.tvdb_pin_entry):
            entry.delete(0, "end")
        try:
            self._persist_config()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Couldn't persist cleared API keys: %s", exc)

    def _clear_banners(self) -> None:
        """Empty the header + footer banner entries and persist."""
        self.header_entry.delete(0, "end")
        self.footer_entry.delete(0, "end")
        try:
            self._persist_config()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Couldn't persist cleared banners: %s", exc)

    def _on_appearance_change(self, mode: str) -> None:
        """Apply the new appearance and persist it for next launch."""
        ctk.set_appearance_mode(mode)
        # Save the FULL form state, not just the appearance — otherwise an
        # in-memory Clear (links, API keys, …) gets silently undone the
        # next time we read the disk and add one field on top.
        try:
            self._persist_config()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Couldn't persist appearance mode: %s", exc)

    def _on_font_choice_changed(self, label: str) -> None:
        """Persist the chosen font family and inform the user a restart is needed."""
        family = self._font_label_to_family.get(label, "Aptos")
        self.font_family_var.set(family)
        # Save the FULL form state. Same reason as _on_appearance_change —
        # reading the disk + re-saving a single field would clobber any
        # pending Clear-button work the user just did in memory.
        try:
            self._persist_config()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Couldn't persist font choice: %s", exc)
        # Hint shown right under the dropdown.
        self.font_hint_label.configure(
            text=f"Font changed to “{family}”. Restart the app for the new font to take effect.",
        )

    def _on_provider_change(self, value: str) -> None:
        """Show only the auth row that matches the selected provider."""
        # TMDB: API key row visible. TVDB: TVDB key + PIN row visible.
        # AniList: neither (no auth needed).
        if value == "TVDB":
            self.tmdb_key_row.grid_remove()
            self.tvdb_key_row.grid()
        elif value == "AniList":
            self.tmdb_key_row.grid_remove()
            self.tvdb_key_row.grid_remove()
        else:  # TMDB or unknown
            self.tmdb_key_row.grid()
            self.tvdb_key_row.grid_remove()

    def _on_date_mode_change(self) -> None:
        self.date_custom_entry.configure(
            state="normal" if self.date_mode.get() == "custom" else "disabled"
        )

    def _on_folder_toggle(self) -> None:
        self._set_folder_children_state(self.folder_var.get())

    def _on_subfolders_toggle(self) -> None:
        self._set_extraction_state(
            self.folder_var.get() and self.subfolders_var.get()
        )

    def _set_folder_children_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.sub_check.configure(state=state)
        self.move_check.configure(state=state)
        if not enabled:
            self.subfolders_var.set(False)
            self.move_video_var.set(False)
        self._set_extraction_state(enabled and self.subfolders_var.get())

    def _set_extraction_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (self.sample_check, self.subs_check, self.screens_check,
                       self.sample_duration_entry, self.screens_count_entry):
            widget.configure(state=state)
        if not enabled:
            self.extract_sample_var.set(False)
            self.extract_subs_var.set(False)
            self.generate_screens_var.set(False)

    # ========================================================================
    # Value resolution helpers (form → arguments)
    # ========================================================================

    def _resolve_release_date(self) -> str | None:
        mode = self.date_mode.get()
        if mode == "today":
            return datetime.now().strftime("%d-%m-%Y")
        if mode == "modified":
            md = self.metadata.get("file_modified_date") if self.metadata else "N/A"
            if md == "N/A":
                messagebox.showerror(
                    "Date error",
                    "No modification date detected. Pick another option.",
                )
                return None
            return md
        custom = self.date_custom_entry.get().strip()
        if not is_valid_release_date(custom):
            messagebox.showerror("Date error", "Invalid date. Use DD-MM-YYYY.")
            return None
        return custom

    def _resolve_source(self) -> str:
        choice = self.source_var.get()
        if choice == "Other":
            return self.source_custom_entry.get().strip() or "Other"
        return choice

    def _resolve_hardcoded_subs(self) -> list[dict]:
        if not self.metadata or self.metadata.get("subtitles"):
            return []
        if not self.hardcoded_var.get():
            return []
        return [{
            "language": self.hc_lang_entry.get().strip() or "Unknown",
            "type":     self.hc_type_var.get() or "Full",
        }]

    def _resolve_links(self) -> dict | None:
        links: dict[str, str] = {}
        for label, entry in self.link_entries.items():
            url = entry.get().strip()
            if url:
                links[label] = url
        return links or None

    def _resolve_int(self, var: ctk.StringVar, default: int, minimum: int = 1) -> int:
        try:
            return max(minimum, int(var.get().strip()))
        except (ValueError, TypeError):
            return default

    # ========================================================================
    # Generate
    # ========================================================================

    def _on_generate(self) -> None:
        if not self.video_path or not self.metadata:
            messagebox.showerror("Nothing to generate", "Load a video file first.")
            return

        release_date = self._resolve_release_date()
        if release_date is None:
            return

        source        = self._resolve_source()
        hardcoded     = self._resolve_hardcoded_subs()
        note          = self.note_entry.get().strip() or DEFAULT_NOTE
        links         = self._resolve_links()
        header_text   = self.header_entry.get().strip() or None
        footer_text   = self.footer_entry.get().strip() or None
        banner_font   = self.banner_font_var.get() or DEFAULT_PYFIGLET_FONT

        # Apply the (TMDB-fetched / manually edited) title + description.
        self.metadata["title"]              = self.title_entry.get().strip()
        self.metadata["description"]        = self.desc_textbox.get("1.0", "end").strip()
        self.metadata["description_source"] = self.desc_source_var.get().strip()

        self._persist_config()

        raw_mediainfo = (
            get_raw_mediainfo_for_path(self.video_path)
            if self.raw_mediainfo_var.get() else None
        )

        try:
            nfo_content = build_nfo(
                self.video_path, self.metadata,
                release_date=release_date,
                source=source,
                hardcoded_subs=hardcoded,
                note=note,
                links=links,
                header_text=header_text,
                footer_text=footer_text,
                banner_font=banner_font,
                raw_mediainfo=raw_mediainfo,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("build_nfo failed")
            messagebox.showerror("Build error", str(exc))
            return

        # ---- Season folder: write inside the folder, no wrapper / extract ---
        if self.is_folder:
            try:
                release_name = os.path.basename(os.path.normpath(self.video_path))
                nfo_path = os.path.join(self.video_path, release_name + ".nfo")
                if not self._confirm_overwrite(nfo_path):
                    return
                with open(nfo_path, "w", encoding="utf-8") as fh:
                    fh.write(nfo_content)
                self._set_status("✓ Season NFO generated.")
                self._show_result(f"✓ Season NFO written to:\n{nfo_path}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to write season NFO")
                messagebox.showerror("Write error", str(exc))
            return

        # ---- Plain NFO (no folder): write synchronously, it's instant -----
        if not self.folder_var.get():
            try:
                nfo_path = os.path.join(
                    os.path.dirname(self.video_path),
                    release_name_from_video(self.video_path) + ".nfo",
                )
                if not self._confirm_overwrite(nfo_path):
                    return
                with open(nfo_path, "w", encoding="utf-8") as fh:
                    fh.write(nfo_content)
                self._set_status("✓ NFO generated.")
                self._show_result(f"✓ NFO written to:\n{nfo_path}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to write NFO")
                messagebox.showerror("Write error", str(exc))
            return

        # ---- Folder + extraction: off-thread, with progress -----------------
        release_name = release_name_from_video(self.video_path)
        target_folder = os.path.join(os.path.dirname(self.video_path), release_name)
        target_nfo = os.path.join(target_folder, release_name + ".nfo")
        if os.path.exists(target_nfo) and not self._confirm_overwrite(target_nfo):
            return

        opts = dict(
            with_subfolders=self.subfolders_var.get(),
            move_video=self.move_video_var.get(),
            extract_sample_flag=self.extract_sample_var.get(),
            sample_duration=self._resolve_int(self.sample_duration_var, 60),
            extract_subs_flag=self.extract_subs_var.get(),
            generate_screens_flag=self.generate_screens_var.get(),
            screens_count=self._resolve_int(self.screens_count_var, 5),
            meta=self.metadata,
        )
        self._begin_progress("Starting…")

        def worker() -> None:
            try:
                result = generate_release_folder(
                    self.video_path, nfo_content,
                    progress_callback=lambda label, frac: self.after(
                        0, self._update_progress, label, frac,
                    ),
                    **opts,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("generate_release_folder failed")
                self.after(0, self._on_folder_error, exc)
                return
            self.after(0, self._on_folder_done, result)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_overwrite(self, target_path: str) -> bool:
        """Return True if it's safe to write (no file there, or user agreed)."""
        if not os.path.exists(target_path):
            return True
        return messagebox.askyesno(
            "Overwrite?",
            f"A file already exists at:\n{target_path}\n\nOverwrite it?",
            default="no",
        )

    # ------------------------------------------------------------------------
    # Progress / result helpers
    # ------------------------------------------------------------------------

    def _begin_progress(self, label: str) -> None:
        self.generate_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.progress_bar.grid()
        self._set_status(label)

    def _update_progress(self, label: str, fraction: float) -> None:
        self.progress_bar.set(max(0.0, min(1.0, fraction)))
        self._set_status(label)

    def _on_folder_done(self, result: dict) -> None:
        self.progress_bar.set(1.0)
        if self.move_video_var.get():
            self.video_path = result["video"]
            self.drop_label.configure(text=f"📁  {os.path.basename(self.video_path)}")

        summary_lines = [
            "Release folder created:",
            f"  {result['folder']}",
            "",
            f"NFO:   {os.path.basename(result['nfo'])}",
            f"Video: {os.path.basename(result['video'])}"
            + ("  (moved)" if self.move_video_var.get() else "  (left in place)"),
        ]
        if result["sample"]:
            summary_lines.append(f"Sample: {os.path.basename(result['sample'])}")
        if result["subs"]:
            summary_lines.append(f"Subs:   {len(result['subs'])} extracted")
        if result["screens"]:
            summary_lines.append(f"Screens: {len(result['screens'])} generated")

        self._set_status("✓ Generation complete.")
        self.generate_btn.configure(state="normal")
        self.progress_bar.grid_remove()
        self._show_result("\n".join(summary_lines))

    def _on_folder_error(self, exc: Exception) -> None:
        self.generate_btn.configure(state="normal")
        self.progress_bar.grid_remove()
        self._set_status("Error.")
        messagebox.showerror("Generation error", str(exc))

    def _set_status(self, text: str, *, busy: bool = False) -> None:
        self.status_label.configure(text=text)
        if busy:
            self.generate_btn.configure(state="disabled")

    def _show_result(self, text: str) -> None:
        self.result_box.grid()
        self.result_box.configure(state="normal")
        self.result_box.delete("1.0", "end")
        self.result_box.insert("1.0", text)
        self.result_box.configure(state="disabled")

    # ========================================================================
    # Live preview rendering
    # ========================================================================

    def _on_tab_changed(self) -> None:
        """Refresh the preview whenever the user lands on the Preview tab."""
        try:
            if self.tabs.get() == "Preview":
                self._render_preview()
        except Exception:  # noqa: BLE001 — defensive (tab may not exist yet)
            pass

    def _schedule_preview_refresh(self, *_args) -> None:
        """Debounced (400 ms) auto-refresh of the preview as the user types."""
        live = getattr(self, "preview_live_var", None)
        if live is None or not live.get():
            return
        if getattr(self, "_preview_after_id", None):
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:  # noqa: BLE001
                pass
        self._preview_after_id = self.after(400, self._render_preview)

    def _preview_release_date(self) -> str:
        """A release date for the preview that never errors (falls back gracefully)."""
        mode = self.date_mode.get()
        if mode == "today":
            return datetime.now().strftime("%d-%m-%Y")
        if mode == "modified":
            md = self.metadata.get("file_modified_date") if self.metadata else "N/A"
            return md if md and md != "N/A" else datetime.now().strftime("%d-%m-%Y")
        custom = self.date_custom_entry.get().strip()
        return custom if is_valid_release_date(custom) else "DD-MM-YYYY"

    def _get_raw_mediainfo_cached(self):
        """One raw-MediaInfo fetch per video path — cached for the session."""
        if not self.video_path:
            return None
        cache = getattr(self, "_raw_mediainfo_cache", None)
        if cache is None:
            cache = self._raw_mediainfo_cache = {}
        if self.video_path not in cache:
            cache[self.video_path] = get_raw_mediainfo_for_path(self.video_path)
        return cache[self.video_path]

    def _render_preview(self) -> None:
        """Rebuild the .nfo from the current form state into the Preview tab."""
        if not hasattr(self, "preview_textbox"):
            return
        if not self.metadata or not self.video_path:
            self._set_preview_text("Load a file or folder to see the preview.")
            return
        try:
            # Build a meta dict with the form overrides applied (title, desc…),
            # then call build_nfo exactly like _on_generate does.
            meta = dict(self.metadata)
            meta["title"]              = self.title_entry.get().strip()
            meta["description"]        = self.desc_textbox.get("1.0", "end").strip()
            meta["description_source"] = self.desc_source_var.get().strip()

            note = self.note_entry.get().strip() or DEFAULT_NOTE
            raw = self._get_raw_mediainfo_cached() if self.raw_mediainfo_var.get() else None

            nfo = build_nfo(
                self.video_path, meta,
                release_date=self._preview_release_date(),
                source=self._resolve_source(),
                hardcoded_subs=self._resolve_hardcoded_subs(),
                note=note,
                links=self._resolve_links(),
                header_text=self.header_entry.get().strip() or None,
                footer_text=self.footer_entry.get().strip() or None,
                banner_font=self.banner_font_var.get() or DEFAULT_PYFIGLET_FONT,
                raw_mediainfo=raw,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Preview build failed: %s", exc)
            self._set_preview_text(f"Preview error: {exc}")
            return
        self._set_preview_text(nfo)

    def _set_preview_text(self, text: str) -> None:
        self.preview_textbox.configure(state="normal")
        self.preview_textbox.delete("1.0", "end")
        self.preview_textbox.insert("1.0", text)
        self.preview_textbox.configure(state="disabled")

    # ========================================================================
    # Inline validation — coloured borders on bad entries
    # ========================================================================

    # CTkEntry border colours: a regular tuple of (light_mode, dark_mode).
    # Inline validation: theme accent for OK / muted red for errors.
    _VALID_BORDER = ("#1FA9A1", "#40CCC4")
    _ERROR_BORDER = ("#D14040", "#E66B6B")

    def _setup_validation(self) -> None:
        """Wire live validation on the entries where it matters most."""
        # Custom date entry (DD-MM-YYYY).
        self.date_custom_entry.bind(
            "<KeyRelease>",
            lambda _e: self._validate_date_field(),
            add="+",
        )
        # Sample duration + screen count (must be positive integers).
        self.sample_duration_entry.bind(
            "<KeyRelease>",
            lambda _e: self._validate_int_field(self.sample_duration_entry),
            add="+",
        )
        self.screens_count_entry.bind(
            "<KeyRelease>",
            lambda _e: self._validate_int_field(self.screens_count_entry),
            add="+",
        )

    def _validate_date_field(self) -> None:
        text = self.date_custom_entry.get().strip()
        valid = (not text) or is_valid_release_date(text)
        self.date_custom_entry.configure(
            border_color=self._VALID_BORDER if valid else self._ERROR_BORDER,
        )

    def _validate_int_field(self, entry) -> None:
        text = entry.get().strip()
        if not text:
            valid = True
        else:
            try:
                valid = int(text) > 0
            except ValueError:
                valid = False
        entry.configure(
            border_color=self._VALID_BORDER if valid else self._ERROR_BORDER,
        )

    def _wire_preview_refresh(self) -> None:
        """Bind every form field to the debounced preview refresh."""
        # Variables: trace_add fires on every .set() call.
        for var in (
            self.source_var, self.date_mode, self.hardcoded_var, self.hc_type_var,
            self.desc_source_var, self.banner_font_var,
            self.folder_var, self.subfolders_var, self.move_video_var,
            self.extract_sample_var, self.extract_subs_var, self.generate_screens_var,
            self.folder_duration_var, self.folder_bitrate_var,
            self.raw_mediainfo_var, self.sample_duration_var, self.screens_count_var,
        ):
            var.trace_add("write", self._schedule_preview_refresh)

        # Entries / text widgets: key release covers typing + paste.
        for entry in (
            self.source_custom_entry, self.date_custom_entry, self.note_entry,
            self.title_entry, self.header_entry, self.footer_entry,
            self.hc_lang_entry,
        ):
            entry.bind("<KeyRelease>", self._schedule_preview_refresh)
        for entry in self.link_entries.values():
            entry.bind("<KeyRelease>", self._schedule_preview_refresh)
        self.desc_textbox.bind("<KeyRelease>", self._schedule_preview_refresh)

    # ========================================================================
    # TMDB lookup pipeline (Auto-search + Fetch by ID + inline picker)
    # ========================================================================

    def _episode_context(self):
        """(season, episode, want_episode) for the current input."""
        if self.is_folder or not self.video_path:
            return None, None, False
        season, episode = guess_season_episode(self.video_path)
        return season, episode, True

    def _fetch_tmdb_by_id(self) -> None:
        """Dispatcher: routes "Fetch by ID" to the currently selected provider."""
        provider = self.provider_var.get()
        if provider == "AniList":
            self._fetch_anilist_by_id()
            return
        if provider == "TVDB":
            self._fetch_tvdb_by_id()
            return
        key = self.tmdb_key_entry.get().strip()
        if not key:
            messagebox.showerror("TMDB", "Enter your TMDB API key first.")
            return
        tmdb_id = self.tmdb_id_entry.get().strip()
        if not tmdb_id:
            messagebox.showerror("TMDB", "Enter a TMDB ID, or use Auto-search.")
            return
        kind = self.tmdb_kind_var.get()
        media_kind = "tv" if kind == "tv" else "movie"
        language = self.tmdb_lang_var.get()
        season, episode, want_episode = self._episode_context()
        self._run_tmdb(lambda: tmdb_lookup(
            key, tmdb_id=tmdb_id, media_kind=media_kind, language=language,
            season=season, episode=episode, want_episode=want_episode,
        ))

    def _fetch_anilist_by_id(self) -> None:
        anilist_id = self.tmdb_id_entry.get().strip()
        if not anilist_id:
            messagebox.showerror("AniList", "Enter an AniList ID, or use Auto-search.")
            return
        try:
            anilist_id_int = int(anilist_id)
        except ValueError:
            messagebox.showerror("AniList", "AniList IDs are integers (e.g. 101921).")
            return
        language = self.tmdb_lang_var.get()
        self._run_tmdb(lambda: anilist_lookup(
            anilist_id=anilist_id_int, language=language,
        ))

    def _fetch_tmdb_auto(self) -> None:
        """Dispatcher: routes "Auto-search" to the currently selected provider."""
        provider = self.provider_var.get()
        if provider == "AniList":
            self._fetch_anilist_auto()
            return
        if provider == "TVDB":
            self._fetch_tvdb_auto()
            return
        key = self.tmdb_key_entry.get().strip()
        if not key:
            messagebox.showerror("TMDB", "Enter your TMDB API key first.")
            return
        if not self.video_path:
            messagebox.showerror("TMDB", "Load a file or folder first.")
            return
        title, year, is_tv = resolve_title_year_from_path(self.video_path)
        if not title:
            messagebox.showerror(
                "TMDB", "Couldn't guess a title from the name/folders. Use Fetch by ID.",
            )
            return
        kind = self.tmdb_kind_var.get()
        media_kind = "tv" if (kind == "tv" or (kind == "auto" and is_tv)) else "movie"
        language = self.tmdb_lang_var.get()
        season, episode, want_episode = self._episode_context()
        self.tmdb_status.configure(
            text=f"Searching TMDB for “{title}”{f' ({year})' if year else ''}…"
        )

        def work() -> None:
            try:
                cands = tmdb_search_candidates(
                    key, query=title, year=year,
                    media_kind=media_kind, language=language,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("TMDB search failed: %s", exc)
                self.after(0, lambda e=exc: self._tmdb_failed(str(e)))
                return
            for cand in cands:
                cand["poster_bytes"] = download_image_bytes(cand.get("poster_url"))
            self.after(0, lambda: self._on_candidates_ready(
                cands, season, episode, want_episode, language,
            ))

        threading.Thread(target=work, daemon=True).start()

    def _fetch_anilist_auto(self) -> None:
        if not self.video_path:
            messagebox.showerror("AniList", "Load a file or folder first.")
            return
        title, _year, _is_tv = resolve_title_year_from_path(self.video_path)
        if not title:
            messagebox.showerror(
                "AniList", "Couldn't guess a title from the name/folders. Use Fetch by ID.",
            )
            return
        language = self.tmdb_lang_var.get()
        self.tmdb_status.configure(text=f"Searching AniList for “{title}”…")

        def work() -> None:
            try:
                cands = anilist_search_candidates(query=title, language=language)
            except Exception as exc:  # noqa: BLE001
                logger.warning("AniList search failed: %s", exc)
                self.after(0, lambda e=exc: self._tmdb_failed(str(e)))
                return
            for cand in cands:
                cand["poster_bytes"] = download_image_bytes(cand.get("poster_url"))
            self.after(0, lambda: self._on_candidates_ready(
                cands, None, None, False, language,
            ))

        threading.Thread(target=work, daemon=True).start()

    # ---- TVDB --------------------------------------------------------------

    def _tvdb_credentials(self):
        """Return (api_key, pin) from the GUI fields, or (None, None) + error."""
        key = self.tvdb_key_entry.get().strip()
        if not key:
            messagebox.showerror("TVDB", "Enter your TVDB API key first.")
            return None, None
        pin = self.tvdb_pin_entry.get().strip()
        return key, pin

    def _fetch_tvdb_by_id(self) -> None:
        key, pin = self._tvdb_credentials()
        if not key:
            return
        tvdb_id = self.tmdb_id_entry.get().strip()
        if not tvdb_id:
            messagebox.showerror("TVDB", "Enter a TVDB ID, or use Auto-search.")
            return
        try:
            tvdb_id_int = int(tvdb_id)
        except ValueError:
            messagebox.showerror("TVDB", "TVDB IDs are integers (e.g. 305074).")
            return
        kind = self.tmdb_kind_var.get()
        media_kind = "tv" if kind != "movie" else "movie"
        language = self.tmdb_lang_var.get()
        season, episode, want_episode = self._episode_context()
        ep_season = season if (media_kind == "tv" and want_episode) else None
        ep_number = episode if (media_kind == "tv" and want_episode) else None
        self._run_tmdb(lambda: tvdb_lookup(
            tvdb_id=tvdb_id_int, api_key=key, pin=pin,
            media_kind=media_kind, language=language,
            season=ep_season, episode=ep_number,
        ))

    def _fetch_tvdb_auto(self) -> None:
        key, pin = self._tvdb_credentials()
        if not key:
            return
        if not self.video_path:
            messagebox.showerror("TVDB", "Load a file or folder first.")
            return
        title, year, is_tv = resolve_title_year_from_path(self.video_path)
        if not title:
            messagebox.showerror(
                "TVDB", "Couldn't guess a title from the name/folders. Use Fetch by ID.",
            )
            return
        kind = self.tmdb_kind_var.get()
        media_kind = "tv" if (kind == "tv" or (kind == "auto" and is_tv)) else "movie"
        language = self.tmdb_lang_var.get()
        season, episode, want_episode = self._episode_context()
        self.tmdb_status.configure(
            text=f"Searching TVDB for “{title}”{f' ({year})' if year else ''}…"
        )

        def work() -> None:
            try:
                cands = tvdb_search_candidates(
                    query=title, api_key=key, pin=pin,
                    year=year, media_kind=media_kind,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("TVDB search failed: %s", exc)
                self.after(0, lambda e=exc: self._tmdb_failed(str(e)))
                return
            for cand in cands:
                cand["poster_bytes"] = download_image_bytes(cand.get("poster_url"))
            self.after(0, lambda: self._on_candidates_ready(
                cands, season, episode, want_episode, language,
            ))

        threading.Thread(target=work, daemon=True).start()

    def _on_candidates_ready(self, candidates, season, episode, want_episode, language):
        if not candidates:
            self._tmdb_failed("No results for that title.")
            return
        if len(candidates) == 1:
            self.tmdb_status.configure(text="1 match found — fetching details…")
            self._apply_candidate(candidates[0], season, episode, want_episode, language)
            return
        self.tmdb_status.configure(
            text=f"Found {len(candidates)} candidates — pick the right one below.",
        )
        self._show_candidate_picker(
            candidates,
            on_select=lambda c: self._apply_candidate(
                c, season, episode, want_episode, language,
            ),
        )
        # Switch to Content tab so the inline picker is in view.
        try:
            self.tabs.set("Content")
        except Exception:  # noqa: BLE001
            pass

    def _apply_candidate(self, candidate, season, episode, want_episode, language):
        """Dispatch the final lookup based on the candidate's provider."""
        self.tmdb_status.configure(text=f"Fetching “{candidate.get('title')}”…")
        provider = candidate.get("provider")
        if provider == "AniList":
            self._run_tmdb(lambda: anilist_lookup(
                anilist_id=int(candidate["id"]), language=language,
            ))
            return
        if provider == "TVDB":
            key = self.tvdb_key_entry.get().strip()
            pin = self.tvdb_pin_entry.get().strip()
            media_kind = candidate.get("kind", "tv")
            ep_season = season if (media_kind == "tv" and want_episode) else None
            ep_number = episode if (media_kind == "tv" and want_episode) else None
            self._run_tmdb(lambda: tvdb_lookup(
                tvdb_id=int(candidate["id"]), api_key=key, pin=pin,
                media_kind=media_kind, language=language,
                season=ep_season, episode=ep_number,
            ))
            return
        key = self.tmdb_key_entry.get().strip()
        self._run_tmdb(lambda: tmdb_lookup(
            key, tmdb_id=str(candidate["id"]),
            media_kind=candidate.get("kind", "movie"),
            language=language, season=season, episode=episode,
            want_episode=want_episode,
        ))

    # ------------------------------------------------------------------------
    # "Include other providers" — fill cross-provider link rows in one click
    # ------------------------------------------------------------------------

    def _fetch_include_others(self) -> None:
        """
        Run lookups on the providers that weren't used as the primary fetch,
        then merge ONLY their owned link rows into the form. Title and
        description are deliberately left untouched.

        Provider eligibility:
          * TMDB    — needs TMDB key (saved in config).
          * AniList — always available (no auth).
          * TVDB    — needs TVDB key.
        Each provider whose credentials are missing is silently skipped.
        """
        # Use the title the user (or the previous fetch) put in the field as
        # the search query, stripping any trailing "(YYYY)" we may have added.
        title_raw = self.title_entry.get().strip()
        if not title_raw:
            messagebox.showerror(
                "Include",
                "No Title to search with — do a primary fetch first, "
                "or type a title manually.",
            )
            return

        import re as _re
        m = _re.search(r"\s*\((\d{4})\)\s*$", title_raw)
        year = m.group(1) if m else ""
        title_for_search = _re.sub(r"\s*\(\d{4}\)\s*$", "", title_raw).strip()

        primary = self.provider_var.get()
        language = self.tmdb_lang_var.get()
        kind = self.tmdb_kind_var.get()
        media_kind = "tv" if kind in ("tv", "auto") else "movie"

        tmdb_key = self.tmdb_key_entry.get().strip()
        tvdb_key = self.tvdb_key_entry.get().strip()
        tvdb_pin = self.tvdb_pin_entry.get().strip()

        # Build the list of secondary providers, skipping the primary
        # and any provider without credentials.
        plan: list[str] = []
        if primary != "TMDB" and tmdb_key:
            plan.append("TMDB")
        if primary != "AniList":
            plan.append("AniList")
        if primary != "TVDB" and tvdb_key:
            plan.append("TVDB")

        if not plan:
            self.tmdb_status.configure(
                text="Nothing to include — other providers have no credentials.",
            )
            return

        self.tmdb_status.configure(
            text=f"Including links from {', '.join(plan)}…",
        )

        def work() -> None:
            results: list[tuple[str, object]] = []
            for prov in plan:
                try:
                    if prov == "TMDB":
                        res = tmdb_lookup(
                            tmdb_key, query=title_for_search,
                            media_kind=media_kind, language=language,
                            year=year or None,
                            season=None, episode=None, want_episode=False,
                        )
                    elif prov == "AniList":
                        res = anilist_lookup(
                            query=title_for_search, language=language,
                        )
                    else:  # TVDB
                        res = tvdb_lookup(
                            query=title_for_search,
                            api_key=tvdb_key, pin=tvdb_pin,
                            media_kind=media_kind, language=language,
                            year=year or None,
                        )
                    results.append((prov, res))
                except Exception as exc:  # noqa: BLE001
                    logger.info("Include %s failed: %s", prov, exc)
                    results.append((prov, exc))
            self.after(0, lambda: self._on_include_done(results))

        threading.Thread(target=work, daemon=True).start()

    def _on_include_done(self,
                         results: list[tuple[str, object]]) -> None:
        """Apply each secondary fetch's managed-label links into the form."""
        ok: list[str] = []
        fail: list[str] = []
        for prov, res in results:
            if isinstance(res, Exception):
                fail.append(f"{prov} ({str(res)[:40]})")
                continue
            managed = res.get("managed_labels") or []  # type: ignore[union-attr]
            links = res.get("links") or {}              # type: ignore[union-attr]
            applied_any = False
            for label in managed:
                entry = self.link_entries.get(label)
                if entry is None:
                    continue
                url = links.get(label) or ""
                if not url:
                    continue
                # Only overwrite empty rows so the user doesn't lose URLs
                # they manually typed in.
                current = entry.get().strip()
                if not current:
                    entry.delete(0, "end")
                    entry.insert(0, url)
                    applied_any = True
            if applied_any:
                ok.append(prov)
            else:
                fail.append(f"{prov} (no new links)")

        parts: list[str] = []
        if ok:
            parts.append(f"✓ Linked: {', '.join(ok)}")
        if fail:
            parts.append(f"× Skipped: {', '.join(fail)}")
        self.tmdb_status.configure(text="  ".join(parts) or "Nothing to include.")

    def _run_tmdb(self, fn) -> None:
        self.tmdb_status.configure(text="Contacting TMDB…")

        def work() -> None:
            try:
                result = fn()
            except TMDBError as exc:
                logger.info("TMDB error: %s", exc)
                self.after(0, lambda e=exc: self._tmdb_failed(str(e)))
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("TMDB call crashed")
                self.after(0, lambda e=exc: self._tmdb_failed(str(e)))
                return
            self.after(0, lambda: self._tmdb_done(result))

        threading.Thread(target=work, daemon=True).start()

    def _tmdb_done(self, result: dict) -> None:
        title = (result.get("title") or "").strip()
        overview = (result.get("overview") or "").strip()
        provider = result.get("provider", "TMDB")
        # Each provider only refreshes the link rows it actually owns, so
        # switching between TMDB ↔ AniList doesn't wipe the other provider's
        # links that the user already populated.
        managed = result.get("managed_labels") or list(self.link_entries.keys())

        if title:
            self.title_entry.delete(0, "end")
            self.title_entry.insert(0, title)

        result_links = result.get("links") or {}
        for label in managed:
            entry = self.link_entries.get(label)
            if entry is None:
                continue
            entry.delete(0, "end")
            url = result_links.get(label)
            if url:
                entry.insert(0, url)

        if overview:
            self.desc_textbox.delete("1.0", "end")
            self.desc_textbox.insert("1.0", overview)
            self.desc_source_var.set(provider)
            self.tmdb_status.configure(text=f"✓ Loaded from {provider}: {title or 'result'}.")
        else:
            self.tmdb_status.configure(
                text=f"✓ Title + links set ({title or 'result'}). No synopsis available.",
            )

    def _tmdb_failed(self, message: str) -> None:
        self.tmdb_status.configure(text=f"TMDB: {message}")

    # ------------------------------------------------------------------------
    # In-window TMDB candidate picker
    # ------------------------------------------------------------------------

    def _show_candidate_picker(self, candidates: list, on_select) -> None:
        for child in self.candidates_frame.winfo_children():
            child.destroy()
        self._candidate_image_refs = []

        try:
            from PIL import Image
            pil_image = Image
        except ImportError:
            pil_image = None

        header = ctk.CTkFrame(self.candidates_frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        header.grid_columnconfigure(0, weight=1)
        # Reflect the actual provider in the header ("Pick the right AniList
        # result" / "Pick the right TMDB result"), rather than hard-coding.
        provider_name = (
            candidates[0].get("provider") if candidates else None
        ) or "TMDB"
        ctk.CTkLabel(
            header, anchor="w", text=f"Pick the right {provider_name} result:",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(header, text="Cancel", width=80,
                      command=self._hide_candidate_picker
                      ).grid(row=0, column=1, sticky="e")

        listing = ctk.CTkScrollableFrame(self.candidates_frame, height=300)
        listing.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        for candidate in candidates:
            self._build_candidate_row(listing, candidate, pil_image, on_select)

        if pil_image is None:
            ctk.CTkLabel(
                self.candidates_frame, anchor="w",
                text="Pillow not installed → text-only picker. Run: pip install pillow",
                text_color=("gray40", "gray60"),
                font=ctk.CTkFont(size=10, slant="italic"),
            ).grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))

        self.candidates_frame.grid()

    def _build_candidate_row(self, parent, candidate, pil_image, on_select) -> None:
        row = ctk.CTkFrame(parent, corner_radius=8)
        row.pack(fill="x", pady=4, padx=2)
        row.grid_columnconfigure(1, weight=1)

        thumb = self._build_candidate_thumb(row, candidate, pil_image)
        thumb.grid(row=0, column=0, padx=8, pady=8, rowspan=2)

        title_text = candidate.get("title") or "(no title)"
        year = candidate.get("year") or ""
        if year:
            title_text = f"{title_text} ({year})"
        kind_pretty = "Movie" if candidate.get("kind") == "movie" else "TV Series"

        ctk.CTkLabel(
            row, anchor="w", text=title_text,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=1, sticky="w", padx=4, pady=(8, 0))
        ctk.CTkLabel(
            row, anchor="w",
            text=f"{kind_pretty} · TMDB ID {candidate.get('id')}",
            text_color=("gray40", "gray70"),
            font=ctk.CTkFont(size=11),
        ).grid(row=1, column=1, sticky="w", padx=4, pady=(0, 8))

        ctk.CTkButton(
            row, text="Select", width=80,
            command=lambda c=candidate: self._candidate_selected(c, on_select),
        ).grid(row=0, column=2, padx=8, rowspan=2)

    def _build_candidate_thumb(self, parent, candidate, pil_image):
        data = candidate.get("poster_bytes")
        if pil_image is not None and data:
            try:
                from io import BytesIO
                img = pil_image.open(BytesIO(data))
                ctk_img = ctk.CTkImage(
                    light_image=img, dark_image=img, size=_CANDIDATE_THUMB_SIZE,
                )
                self._candidate_image_refs.append(ctk_img)
                return ctk.CTkLabel(parent, text="", image=ctk_img)
            except Exception:  # noqa: BLE001
                pass
        marker = "🎬" if candidate.get("kind") == "movie" else "📺"
        return ctk.CTkLabel(
            parent, text=marker, width=_CANDIDATE_THUMB_SIZE[0],
            font=ctk.CTkFont(size=28),
        )

    def _candidate_selected(self, candidate, on_select) -> None:
        self._hide_candidate_picker()
        try:
            on_select(candidate)
        except Exception:  # noqa: BLE001
            pass

    def _hide_candidate_picker(self) -> None:
        for child in self.candidates_frame.winfo_children():
            child.destroy()
        self._candidate_image_refs = []
        self.candidates_frame.grid_remove()


# =============================================================================
# Public entry point — called from NFO_generator.main()
# =============================================================================

def launch_gui(initial_path: str | None = None) -> int:
    try:
        app = NFOApp(initial_path=initial_path)
        app.mainloop()
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("GUI crashed")
        print(f"[!] GUI error: {exc}", file=sys.stderr)
        return 1
