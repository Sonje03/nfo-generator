"""
Unit tests for the three metadata providers (TMDB, AniList, TVDB).

The real APIs aren't hit — every test patches ``urllib.request.urlopen``
to return a canned JSON payload. The goal is to lock down:

  * The provider returns the unified result dict shape
    ``{title, overview, kind, id, links, provider, managed_labels}``.
  * Year is appended to the title (movies + series) but not to episodes.
  * Cross-references (TMDB ↔ TVDB ↔ iMDB) land in the right link rows
    *without* clobbering rows owned by another provider.
  * The TVDB episode lookup defends against the API ignoring its
    ``season=&episodeNumber=`` filter.

These tests use stubs for ``langcodes`` and ``pymediainfo`` so they run
in any environment — no native deps needed.
"""

from __future__ import annotations

import io
import json
import sys
import types
from unittest.mock import patch

import pytest


# ----- Stub out the C-extension deps the providers transitively import.  ------
# This runs before `nfo_generator` is imported, so the real C libs are
# never touched.
def _install_stubs():
    if "langcodes" not in sys.modules:
        lc = types.ModuleType("langcodes")
        class _Lang:
            @classmethod
            def get(cls, code):
                obj = cls(); obj._code = code; return obj
            def display_name(self, *_a, **_k): return self._code
            def is_valid(self): return True
            language_name = lambda self, *_a, **_k: self._code  # noqa: E731
        lc.Language = _Lang
        sys.modules["langcodes"] = lc
    if "pymediainfo" not in sys.modules:
        pmi = types.ModuleType("pymediainfo")
        class _MI:
            tracks = []
            @classmethod
            def parse(cls, *_a, **_k): return cls()
        pmi.MediaInfo = _MI
        sys.modules["pymediainfo"] = pmi


_install_stubs()

from nfo_generator import (  # noqa: E402
    TMDBError,
    anilist_lookup,
    tmdb_lookup,
    tvdb_lookup,
)
from nfo_generator import providers as _providers  # noqa: E402


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class _FakeResponse:
    """Minimal stand-in for the object returned by `urllib.request.urlopen`."""

    def __init__(self, payload: dict | str, status: int = 200):
        if isinstance(payload, dict):
            self._body = json.dumps(payload).encode("utf-8")
        else:
            self._body = payload.encode("utf-8")
        self.status = status

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return self._body


def _fake_urlopen(responses):
    """
    Build a urlopen replacement that yields pre-canned responses in order.

    Pass either a single dict (one shot) or a list of dicts (one per call).
    """
    if not isinstance(responses, list):
        responses = [responses]
    iterator = iter(responses)

    def _inner(request, *_, **__):
        try:
            payload = next(iterator)
        except StopIteration:
            raise AssertionError(f"Unexpected extra HTTP call: {getattr(request, 'full_url', request)}")
        return _FakeResponse(payload)

    return _inner


# --------------------------------------------------------------------------
# TMDB
# --------------------------------------------------------------------------

class TestTmdbLookup:
    def test_returns_unified_shape_for_movie(self):
        # /search/movie → /movie/123 → /movie/123/external_ids
        responses = [
            {"results": [{"id": 123, "title": "Demo Movie", "release_date": "2024-05-01"}]},
            {"id": 123, "title": "Demo Movie", "overview": "A demo.", "release_date": "2024-05-01"},
            {"imdb_id": "tt9999999"},
        ]
        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(responses)):
            _providers._TMDB_LOOKUP_CACHE.clear()
            result = tmdb_lookup("FAKE_KEY", query="Demo Movie", media_kind="movie",
                                 want_episode=False)

        assert result["title"] == "Demo Movie (2024)"
        assert result["overview"] == "A demo."
        assert result["kind"] == "movie"
        assert result["provider"] == "TMDB"
        assert "TMDB...........:" in result["managed_labels"]
        assert result["links"]["iMDB...........:"] == "https://www.imdb.com/title/tt9999999/"

    def test_episode_overrides_show_title(self):
        # /search/tv → /tv/55 → /tv/55/season/1/episode/2 → /tv/55/external_ids
        responses = [
            {"results": [{"id": 55, "name": "Demo Show", "first_air_date": "2023-01-01"}]},
            {"id": 55, "name": "Demo Show", "overview": "Series synopsis.", "first_air_date": "2023-01-01"},
            {"name": "Episode Title", "overview": "Episode synopsis."},
            {"imdb_id": "tt1234567", "tvdb_id": 9999},
        ]
        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(responses)):
            _providers._TMDB_LOOKUP_CACHE.clear()
            result = tmdb_lookup("KEY", query="Demo Show", media_kind="tv",
                                 season=1, episode=2, want_episode=True)

        # Title becomes episode name, overview switches to episode synopsis,
        # year is NOT appended because used_episode is True.
        assert result["title"] == "Episode Title"
        assert result["overview"] == "Episode synopsis."
        assert "(2023)" not in result["title"]


# --------------------------------------------------------------------------
# AniList
# --------------------------------------------------------------------------

class TestAnilistLookup:
    def test_anilist_returns_unified_shape(self):
        graphql_payload = {
            "data": {
                "Media": {
                    "id": 1010,
                    "title": {"romaji": "Demo Anime", "english": None, "native": None},
                    "description": "Anime synopsis.",
                    "startDate": {"year": 2020},
                    "format": "TV",
                    "idMal": 50000,
                    "siteUrl": "https://anilist.co/anime/1010",
                }
            }
        }
        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(graphql_payload)):
            result = anilist_lookup(anilist_id=1010)

        assert result["provider"] == "AniList"
        assert result["title"].startswith("Demo Anime")
        assert "ANiLiST........:" in result["managed_labels"]
        assert "MAL............:" in result["managed_labels"]
        assert result["links"]["MAL............:"] == "https://myanimelist.net/anime/50000"

    def test_anilist_strips_html(self):
        graphql_payload = {
            "data": {
                "Media": {
                    "id": 7,
                    "title": {"romaji": "Show", "english": None, "native": None},
                    "description": "Line one.<br><br>Line two.",
                    "startDate": {"year": 2024},
                    "format": "TV",
                    "siteUrl": "https://anilist.co/anime/7",
                }
            }
        }
        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(graphql_payload)):
            result = anilist_lookup(anilist_id=7)
        assert "<br>" not in result["overview"]
        assert "Line one." in result["overview"]


# --------------------------------------------------------------------------
# TVDB
# --------------------------------------------------------------------------

class TestTvdbLookup:
    def test_tvdb_episode_filter_resilient(self):
        """Even if TVDB ignores ?season=&episodeNumber=, we must pick the
        exact episode, not the first one in the paginated list."""
        responses = [
            # POST /login — TVDB requires `status:"success"` + `data.token`.
            {"status": "success", "data": {"token": "FAKE_TOKEN"}},
            # GET /search?query=Demo&type=series → 1 result (auto-pick path)
            {"data": [
                {"tvdb_id": 42, "name": "Demo Series",
                 "type": "series", "year": "2022"},
            ]},
            # GET /series/42/extended
            {"data": {
                "id": 42, "name": "Demo Series",
                "overview": "Series-level overview.",
                "firstAired": "2022-03-15",
                "translations": {"nameTranslations": [], "overviewTranslations": []},
                "remoteIds": [{"sourceName": "IMDB", "id": "tt9999999"}],
            }},
            # GET /series/42/episodes/default?season=1&episodeNumber=3
            # The API ignored our filter and returns the whole list — we must
            # still pick episode 3 specifically.
            {"data": {"episodes": [
                {"id": 1, "seasonNumber": 1, "number": 1, "name": "Pilot",
                 "overview": "Pilot synopsis."},
                {"id": 2, "seasonNumber": 1, "number": 2, "name": "Two",
                 "overview": "Episode 2 synopsis."},
                {"id": 3, "seasonNumber": 1, "number": 3, "name": "Three",
                 "overview": "Episode 3 synopsis."},
            ]}},
        ]
        # Reset cached TVDB token so /login is hit fresh.
        _providers._TVDB_TOKEN = None
        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(responses)):
            result = tvdb_lookup(api_key="FAKE", query="Demo",
                                  media_kind="tv", season=1, episode=3)

        assert result["title"] == "Three"
        assert result["overview"] == "Episode 3 synopsis."
        assert result["provider"] == "TVDB"

    def test_tvdb_missing_api_key_raises(self):
        with pytest.raises(TMDBError):
            tvdb_lookup(api_key="", media_kind="movie", tvdb_id=1)
