#!/usr/bin/env python3
"""Compatibility shim: forwards to the ``nfo_generator`` package.

The implementation lives in ``nfo_generator/`` (split across core,
providers, nfo, extraction). This file exists so existing entry points —
``python NFO_generator.py``, the PyInstaller spec, IDE run configs — keep
working without touching the package layout.
"""

from __future__ import annotations

# Re-export the public API for any script doing ``from NFO_generator import …``.
from nfo_generator import *           # noqa: F401, F403
from nfo_generator import logger      # noqa: F401  (explicit: not in __all__)
from nfo_generator.extraction import main


if __name__ == "__main__":
    main()
