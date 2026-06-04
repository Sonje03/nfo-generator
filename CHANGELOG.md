# Changelog

Format loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- TVDB v4 provider: search, episode-level lookup, `/translations/{lang}`.
- AniList provider via GraphQL (no auth).
- Provider dropdown in the Content tab (TMDB / AniList / TVDB).
- "Include other providers" button — runs the other two with the current
  title and fills empty link rows. Title and description stay untouched.
- Unified provider schema: every lookup returns
  `{title, overview, kind, id, links, provider, managed_labels}`.
- Package layout under `nfo_generator/` (core / nfo / providers /
  extraction). `NFO_generator.py` at the root is now a small compat shim.
- TypedDict for `MetaDict` and tracks.
- App icon (`assets/icon.png` + `.icns` + `.ico`), generated from
  `assets/make_icons.py`. Shows up in the title bar too.
- Custom teal-on-dark-navy theme (`assets/theme.json`).
- UI font picker. Default = Press Start 2P. Drop a TTF into
  `assets/fonts/` and it's registered at startup via CoreText / GDI /
  fontconfig (ctypes, no extra deps).
- Dark mode default, persisted across sessions.
- Update check at launch, 24h cache, dismissible banner.
- `CHANGELOG.md`, `CONTRIBUTING.md`.
- `pyproject.toml` with PyPI metadata and a `nfo-generator` console
  entry point.
- Pre-commit config with ruff + mypy.

### Changed

- `wrap_text` honors `\n` paragraph breaks. Multi-paragraph descriptions
  from AniList / TVDB no longer bleed past the box border.
- TVDB title uses the episode name when an episode resolves, matching
  the TMDB behavior. The series-premiere year suffix is dropped in that
  case.
- Candidate picker header reflects the actual primary provider (was
  hardcoded to "TMDB").
- Network errors get human messages instead of raw urllib stacks
  ("Network unreachable", "Connection refused", etc.).

### Removed

- The template system (`TEMPLATE_CHOICES`, `DEFAULT_TEMPLATE`, the
  "compact" / "minimal" alternatives and their helpers). Single
  canonical rendering now.
- `tmdb_download_poster` (replaced by the generic `download_image_bytes`).

### Fixed

- TVDB language: the extended endpoint exposes
  `nameTranslations` / `overviewTranslations` as lists of available
  codes, not dicts of `{code: text}`. Translations are now fetched
  explicitly via `/series|movies/{id}/translations/{lang}`.
- TVDB episode lookup: `?season=N&episodeNumber=M` is unreliable on the
  localized endpoint. The language-less variant is queried instead, with
  a defensive exact-match filter in Python.
- AniList 403 from Cloudflare default UA. Real User-Agent +
  `Accept-Encoding: identity`.
- Credentials weren't persisted unless Generate was clicked. Now saved on
  FocusOut and on window close.
- Clear (links / API keys / banners) state was lost when the font picker
  triggered a partial config save. Handlers now go through
  `_persist_config()` which captures the full form.

## [1.0.0-beta]

First public release.

- MediaInfo-driven `.nfo` rendering with HDR-aware video section, audio,
  subs, chapters, attachments.
- CustomTkinter GUI with native drag & drop, tabs, candidate picker,
  live preview.
- Folder mode (one `.nfo` per release folder using the season's
  metadata).
- Sample / subs / screenshots extraction via bundled `imageio-ffmpeg`.
- TMDB v3 integration: search with poster previews, episode-level
  synopsis on `SxxExx`, IMDb / TVDB cross-references from
  `external_ids`.
- Cross-platform (macOS, Windows, Linux). `libmediainfo` auto-discovered
  or bundled into `./lib/`.

[Unreleased]: https://github.com/Sonje03/nfo-generator/compare/v1.0.0-beta...HEAD
[1.0.0-beta]: https://github.com/Sonje03/nfo-generator/releases/tag/v1.0.0-beta
