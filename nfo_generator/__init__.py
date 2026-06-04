"""NFO Generator — public API entry point.

Importing from ``nfo_generator`` gives you everything the old single-file
``NFO_generator.py`` script exposed, so existing tooling (the GUI in
``nfo_gui.py``, downstream tests, PyInstaller specs) keeps working
without changes.
"""

from __future__ import annotations

__version__ = "1.0.0-beta"

from .core import *       # noqa: F401, F403
from .nfo import *        # noqa: F401, F403
from .providers import *  # noqa: F401, F403
from .extraction import * # noqa: F401, F403
from .types import (      # noqa: F401  (TypedDicts for the meta dict)
    AudioTrack, LinksDict, MetaDict, SubtitleTrack,
)
from .update_check import check_for_update_async  # noqa: F401

# Helpers / objects that don't have a public name in __all__ but are still
# imported by ``nfo_gui.py`` and downstream tools.
from .core import logger, MEDIAINFO_LIB_CANDIDATES  # noqa: F401
