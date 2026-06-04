# Changelog

Format loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

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

[Unreleased]: https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta...HEAD
[1.0.0-beta]: https://github.com/Sonje03/nfo-generator/releases/tag/v1.0.0-beta
