"""Best-effort "new version available" check against GitHub Releases.

The check is fire-and-forget: it runs in a background thread on app
startup, hits the GitHub API at most once per 24 hours (the result is
cached on disk), and surfaces a non-blocking notification through a
callback the GUI provides. Network failures are silent — the app must
always launch even if the user is offline.

Usage from the GUI
------------------
::

    from nfo_generator.update_check import check_for_update_async

    def show_update_banner(latest: str, url: str) -> None:
        # Called on the Tk main thread via after(0, ...).
        ...

    check_for_update_async(
        repo="Sonje03/nfo-generator",
        current_version=__version__,
        on_update=show_update_banner,
        ui_thread_dispatch=self.after,  # tk's `widget.after`
    )

Disable
-------
The GUI exposes a ``check_updates_on_startup`` boolean in the user
config; when False, `check_for_update_async` is simply not called.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

logger = logging.getLogger("nfo_generator.update_check")

# Cache file lives next to the user config so cleanup is symmetrical.
_CACHE_PATH = Path.home() / ".nfo_generator_update_cache.json"
# Re-poll GitHub at most once per day to stay below their unauthenticated
# rate limit (60 requests / hour, but per-IP).
_CACHE_TTL_SECONDS = 24 * 3600
# Hard cap on the network call so the launch never hangs.
_REQUEST_TIMEOUT = 5

# ``v1.2.3-beta.4`` → (1, 2, 3, "beta.4"). Returns None for unparseable.
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+](.+))?$")


def _parse_version(tag: str) -> Optional[Tuple[int, int, int, str]]:
    """Crude SemVer-ish parser — enough for comparison purposes."""
    match = _VERSION_RE.match(tag.strip())
    if not match:
        return None
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), (suffix or "")


def _is_newer(latest: str, current: str) -> bool:
    """
    True iff ``latest`` is strictly greater than ``current``.

    Pre-release tags (``-beta``, ``-rc``) compare *less than* the same
    base version: ``1.0.0`` > ``1.0.0-beta``. Mirrors how SemVer ranks
    pre-releases below their final.
    """
    lp = _parse_version(latest)
    cp = _parse_version(current)
    if lp is None or cp is None:
        # Fall back to plain string compare when either tag is exotic.
        return latest != current and latest > current
    l_base = lp[:3]
    c_base = cp[:3]
    if l_base != c_base:
        return l_base > c_base
    # Same base version → a release with no suffix beats one with a suffix.
    l_suffix, c_suffix = lp[3], cp[3]
    if l_suffix == c_suffix:
        return False
    if not l_suffix:        # latest is final, current is pre-release
        return True
    if not c_suffix:        # current is final, latest is pre-release
        return False
    return l_suffix > c_suffix


def _load_cache() -> dict:
    try:
        if _CACHE_PATH.is_file():
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_cache(payload: dict) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def _fetch_latest_release(repo: str) -> Optional[dict]:
    """
    GET https://api.github.com/repos/{repo}/releases/latest

    Returns the JSON body on success, ``None`` otherwise.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept":     "application/vnd.github+json",
            "User-Agent": "NFO-Generator-update-check/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.debug("Update check failed for %s: %s", repo, exc)
        return None


def check_for_update_async(
    repo: str,
    current_version: str,
    on_update: Callable[[str, str], None],
    *,
    ui_thread_dispatch: Optional[Callable] = None,
    force: bool = False,
) -> None:
    """
    Kick off a background update check.

    Parameters
    ----------
    repo
        ``"<owner>/<name>"`` — the GitHub repository to poll.
    current_version
        The version the running app reports (e.g. ``"1.0.0-beta"``).
    on_update
        Callback invoked when a newer release is available. Receives
        ``(latest_version, release_url)``. Called on the UI thread via
        ``ui_thread_dispatch`` when provided.
    ui_thread_dispatch
        Usually ``tk_widget.after`` — a function that schedules a callable
        to run on the Tk main loop. When omitted, ``on_update`` is
        called directly from the background thread (acceptable for
        headless / CLI scenarios but unsafe for Tk widgets).
    force
        Skip the 24-hour disk cache. Useful for "Check for updates now"
        menu items.
    """
    def _run() -> None:
        now = time.time()
        cache = _load_cache()
        if not force and (now - cache.get("checked_at", 0) < _CACHE_TTL_SECONDS):
            latest = cache.get("latest_tag") or ""
            url    = cache.get("html_url") or ""
        else:
            payload = _fetch_latest_release(repo) or {}
            latest  = (payload.get("tag_name") or "").strip()
            url     = (payload.get("html_url") or "").strip()
            if latest:  # only refresh cache on success
                _save_cache({
                    "checked_at": now,
                    "latest_tag": latest,
                    "html_url":   url,
                })
        if latest and _is_newer(latest, current_version):
            if ui_thread_dispatch is not None:
                ui_thread_dispatch(0, lambda: on_update(latest, url))
            else:
                on_update(latest, url)

    threading.Thread(target=_run, daemon=True, name="update-check").start()


__all__ = ["check_for_update_async"]
