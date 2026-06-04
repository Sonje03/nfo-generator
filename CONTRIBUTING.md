# Contributing

## Quick start

```bash
git clone https://github.com/Sonje03/nfo-generator.git
cd nfo-generator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Then drop a `libmediainfo` into `./lib/` (see README) and run:

```bash
python NFO_generator.py            # GUI
python NFO_generator.py --cli      # CLI
```

## Project layout

```
nfo_generator/
├── __init__.py            # public API
├── __main__.py            # python -m nfo_generator entry
├── core.py                # constants, config, libmediainfo, formatting
├── nfo.py                 # collect_metadata, build_nfo, banner
├── providers.py           # TMDB / AniList / TVDB
└── extraction.py          # folder + ffmpeg + CLI
NFO_generator.py           # compat shim (forwards to the package)
nfo_gui.py                 # GUI
assets/                    # icons + theme + fonts
tests/
```

`NFO_generator.py` at the root is a 19-line shim. Existing entry points
(`python NFO_generator.py`, PyInstaller, IDE run configs) keep working
unchanged.

`collect_metadata()` consumes a pymediainfo object and returns a plain
dict. GUI and CLI both read from that dict, so pymediainfo types don't
leak past it. `build_nfo()` is pure: data in, `.nfo` string out, no I/O.
`generate_release_folder()` is the only filesystem mutator besides the
final `.nfo` write.

## Building binaries

PyInstaller only builds for the OS it runs on. The repo ships a GitHub
Actions workflow (`.github/workflows/release.yml`) that builds both
macOS and Windows on a tag push, but you can do it manually:

```bash
# macOS
FFMPEG=$(python -c "import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())")
pyinstaller --onedir --windowed --name "NFO Generator" \
    --icon assets/icon.icns \
    --add-binary "lib/libmediainfo.dylib:lib" \
    --add-binary "$FFMPEG:imageio_ffmpeg/binaries" \
    NFO_generator.py
```

```powershell
# Windows
$FFMPEG = python -c "import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())"
pyinstaller --onedir --windowed --name "NFO Generator" `
    --icon assets\icon.ico `
    --add-binary "lib\MediaInfo.dll;lib" `
    --add-binary "$FFMPEG;imageio_ffmpeg/binaries" `
    NFO_generator.py
```

Output goes to `dist/`. `--onedir` starts faster than `--onefile` for
GUI apps.

## Tests

```bash
pytest -q
```

`tests/` covers the pure helpers (`format_*`, `wrap_text`, codec labels,
HDR parsing, title resolution). Provider lookups are tested with mocked
`urllib` — no real API calls.

## Code style

- `ruff` for lint + format (line length 100).
- `mypy` strict on `nfo_generator/` (the GUI uses too much CTk runtime
  API to be worth typing aggressively).
- English comments. Explain the *why* when it's not obvious from the code.
- Use the `logger` from `nfo_generator.core` instead of `print`.

### Pre-commit

```bash
pip install pre-commit
pre-commit install
```

Then `git commit` runs ruff + mypy automatically. To run everything on
the whole repo:

```bash
pre-commit run --all-files
```

## PRs

1. Open an issue first for anything non-trivial.
2. Branch off `main`. Short kebab-case branch names (`tvdb-episode-fix`,
   `gui-batch-mode`).
3. One logical change per PR.
4. Add a line in `CHANGELOG.md` under `[Unreleased]`.
5. `pytest -q` and `pre-commit run --all-files` must pass.

## Bug reports

Include:

- OS + Python version
- The file/folder that triggered it (if you can share)
- Expected vs actual output (paste the `.nfo` or attach a screenshot)
- Terminal logs (the GUI prints to stdout)

## Adding a provider

See `tvdb_lookup` in `nfo_generator/providers.py` for a complete example.
You need:

1. `xxx_search_candidates(query, …) -> list[dict]` returning candidates
   with the same keys as the others (`id`, `kind`, `title`, `year`,
   `overview`, `poster_url`, `provider`).
2. `xxx_lookup(...) -> dict` returning
   `{overview, title, kind, id, links, provider, managed_labels}`.
3. Add the provider name to the GUI's provider dropdown and route it
   through `_apply_candidate`.
4. New auth fields go into `DEFAULT_USER_CONFIG` plus a GUI row.

`managed_labels` is the list of link rows the provider owns. The apply
pipeline only refills those rows, so providers don't clobber each
other's data.

## Code of conduct

Be decent. Disagreements about design are fine, personal attacks aren't.
