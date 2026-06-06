# Changelog

Format loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [1.0.0-beta.10]

### Fixed
- Include other providers always overwrites the link rows it owns
  instead of skipping rows that already had a value. Re-clicking
  Include on a second file no longer reports "no new links".
- Include other providers uses the show name derived from the filename
  as its search query, not the Title field. After a TMDB episode
  lookup the Title shows the episode name, which TVDB / AniList
  couldn't match against a series.
- Diagnostic modal shows the app icon in its title bar on Windows.
  CTk's internal toplevel setup was overwriting iconbitmap; we
  re-apply it three times at staggered delays so at least one lands
  after CTk finishes.

### Internal
- Release workflow has a manual `workflow_dispatch` trigger so preview
  builds can be requested from the Actions tab without tagging.

## [1.0.0-beta.9]

Published with a bug in Include other providers (link rows were
skipped on re-runs). Use beta.10 instead.

## [1.0.0-beta.8]

### Fixed
- Update banner pushed the whole UI down on macOS. The banner now
  lives in a dedicated top slot that doesn't disturb the main grid.
- Pre-release users (anyone on a `-beta` build) couldn't see updates.
  GitHub's `/releases/latest` endpoint skips pre-releases by design,
  so users on beta.6 silently never got notified about beta.7. The
  check auto-opts into pre-releases when the running build itself is
  a pre-release.
- Windows font sizes were over-corrected in beta.7. Press Start 2P
  glyphs were getting clipped vertically and VT323 / Silkscreen /
  Pixelify were too small to read. Each Windows override bumped up
  one notch.
- Diagnostic modal showed Tk's default feather icon instead of the
  app icon (first attempt — still didn't stick on Windows, properly
  fixed in beta.10).

## [1.0.0-beta.7]

### Fixed
- Pixel and display fonts were rendering noticeably bigger on Windows
  than on macOS at the same point size. Added per-platform size
  overrides for the 6 pixel / display faces.

### Documentation
- New README section explaining that block characters look broken in
  Windows Notepad because of font spacing, not because the file is
  corrupt. Recommends Cascadia Code, JetBrains Mono, or Lucida
  Console for a clean render.

## [1.0.0-beta.6]

### Added
- "Show startup diagnostic" button in Settings → Diagnostics. Opens a
  modal with the captured startup log (which bundled fonts the OS
  registered, which ones Tk catalogued, sys.platform, Tk version)
  plus a "Copy to clipboard" button. The only way to inspect runtime
  state on Windows where the packaged app runs without a console.

## [1.0.0-beta.5]

### Fixed
- "Append raw MediaInfo dump" was leaking the full file path on
  Windows (`D:/<username>/Videos/...`). The path is now reduced to
  the basename on all platforms. macOS users were already protected.
- Windows console flashes during sample / subs / screenshots
  extraction. `creationflags=CREATE_NO_WINDOW` is now set on Windows
  subprocess.run calls.
- Preview pane was unreadable on Windows — box-drawing characters
  (█ ▓ ▒ ░) rendered as tofu rectangles because the preview was
  inheriting the UI pixel font. The preview now forces a system
  monospace family regardless of the UI font.
- Windows taskbar / title bar icon uses `iconbitmap(.ico)` instead of
  `iconphoto(PNG)`, which Tk on Windows reliably ignores.

## [1.0.0-beta.4]

### Added
- Settings tab with update preferences:
  - "Check for updates on startup" toggle
  - "Include pre-releases (beta / rc / alpha)" toggle
  - "Check now" button (bypasses the 24-hour cache)
- Pre-release channel support in the update checker. When opted in,
  fetches the full release list and picks the highest version
  including beta / rc / alpha tags.

## [1.0.0-beta.3]

### Fixed
- macOS / Windows binaries now properly bundle the pixel fonts,
  custom theme, and pyfiglet font data. The pixel UI and ASCII
  header banner show up correctly in the packaged app.
- macOS "App is damaged" error: the bundle is now ad-hoc signed, so
  Gatekeeper falls back to the normal "unidentified developer"
  warning that can be bypassed with right-click → Open.
- About box shows the right version and copyright (was "0.0.0").
- macOS CI: the `libmediainfo` path mismatch that crashed the build
  is fixed; the workflow picks whatever versioned `.dylib` brew ships.
- Windows CI: switched to the `mediainfo` chocolatey package that
  actually ships `MediaInfo.dll`.

## [1.0.0-beta.2]

### Fixed
- macOS first build crashed because PyInstaller couldn't find
  `lib/libmediainfo.dylib` (brew installs the versioned file).
- Windows first build was missing `MediaInfo.dll` (the `mediainfo-cli`
  chocolatey package only ships the CLI exe).

## [1.0.0-beta]

First public release.

### Metadata providers
- TMDB v3 lookup (search + episode-level on `SxxExx`, IMDb / TVDB
  cross-references via `external_ids`).
- AniList GraphQL lookup (no auth, fills MAL link too).
- TVDB v4 lookup (search, `/translations/{lang}`, episode-level with a
  defensive `seasonNumber` / `number` filter for when the API ignores
  query params).
- "Include other providers" button merges the remaining two providers'
  link rows without touching title or description.
- Unified result schema across providers:
  `{title, overview, kind, id, links, provider, managed_labels}`.

### Output
- MediaInfo-driven `.nfo` rendering with HDR-aware video section,
  audio, subs, chapters, attachments.
- Folder mode aggregates a season pack into one `.nfo`.
- Sample / subs / screenshots extraction via bundled `imageio-ffmpeg`
  (sample clip, embedded subtitle tracks, timecoded screenshots).
- Configurable pyfiglet header / footer banners.
- Optional raw MediaInfo dump appended below the box.

### UI
- CustomTkinter GUI with native drag & drop, five tabs, candidate
  picker, live preview.
- Pixel + dark-teal theme matching the icon.
- Font picker with Press Start 2P as default. Drop a TTF into
  `assets/fonts/` and it's registered at process level (CoreText / GDI
  / fontconfig via ctypes).
- Dark mode default, persisted across sessions.
- Keyboard shortcuts: ⌘G generate, ⌘O open, ⌘⇧F auto-search.
- Update check on launch (24h cache, dismissible banner).
- Network errors surface as readable messages instead of urllib stacks.

### Project
- Package layout under `nfo_generator/` (core / nfo / providers /
  extraction). `NFO_generator.py` at the root is a small compat shim.
- TypedDict for `MetaDict` and tracks.
- App icon generated from `assets/make_icons.py`.
- `pyproject.toml` with PyPI metadata and a `nfo-generator` console
  entry point.
- Pre-commit config with ruff + mypy.
- GitHub Actions workflow builds macOS + Windows binaries on every
  `v*` tag.

### Cross-platform
- macOS, Windows, Linux. `libmediainfo` auto-discovered or bundled
  into `./lib/`.

[Unreleased]:    https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta.10...HEAD
[1.0.0-beta.10]: https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta.9...v1.0.0-beta.10
[1.0.0-beta.9]:  https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta.8...v1.0.0-beta.9
[1.0.0-beta.8]:  https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta.7...v1.0.0-beta.8
[1.0.0-beta.7]:  https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta.6...v1.0.0-beta.7
[1.0.0-beta.6]:  https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta.5...v1.0.0-beta.6
[1.0.0-beta.5]:  https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta.4...v1.0.0-beta.5
[1.0.0-beta.4]:  https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta.3...v1.0.0-beta.4
[1.0.0-beta.3]:  https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta.2...v1.0.0-beta.3
[1.0.0-beta.2]:  https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta...v1.0.0-beta.2
[1.0.0-beta]:    https://github.com/Sonje03/nfo-generator/releases/tag/v1.0.0-beta
