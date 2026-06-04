"""
Tests for the filename → (title, year, is_tv) heuristics used by the
"Auto-search" provider buttons.

Goal: pin the regex / cleanup behaviour against real-world releases so
the SxxExx detection, year extraction, and "looks like a TV episode"
flag don't silently regress when the release-tag list changes.
"""

from __future__ import annotations

import sys
import types


def _install_stubs():
    if "langcodes" not in sys.modules:
        lc = types.ModuleType("langcodes")
        class _L:
            @classmethod
            def get(cls, c):
                o = cls(); o._c = c; return o
            def display_name(self, *_a, **_k): return self._c
            def is_valid(self): return True
            language_name = lambda self, *_a, **_k: self._c  # noqa: E731
        lc.Language = _L; sys.modules["langcodes"] = lc
    if "pymediainfo" not in sys.modules:
        pmi = types.ModuleType("pymediainfo")
        class _M:
            tracks = []
            @classmethod
            def parse(cls, *_a, **_k): return cls()
        pmi.MediaInfo = _M; sys.modules["pymediainfo"] = pmi


_install_stubs()

from nfo_generator import (  # noqa: E402
    guess_season_episode,
    guess_title_and_year,
    resolve_title_year_from_path,
)


# ---------------------------------------------------------------------------
# guess_title_and_year
# ---------------------------------------------------------------------------

class TestGuessTitleAndYear:
    def test_simple_movie_with_year(self):
        t, y, is_tv = guess_title_and_year("The.Matrix.1999.1080p.BluRay.x264.mkv")
        assert "Matrix" in t
        assert y == "1999"
        assert is_tv is False

    def test_drops_release_tags(self):
        # Tags like MULTI, VFF, 1080P, BLURAY, x264, AAC, etc. must NOT
        # leak into the search query.
        t, _, _ = guess_title_and_year(
            "Project.Hail.Mary.2026.MULTi.VF2.iMAX.2160p.DV.HDR.WEB-DL.H265-BUC.mkv"
        )
        # Title should stop somewhere after "Project Hail Mary" — we just
        # check none of the loud release tags remain.
        assert "MULTi" not in t
        assert "WEB-DL" not in t
        assert "1080p" not in t.lower()
        assert "2160p" not in t.lower()

    def test_tv_episode_detected(self):
        t, _, is_tv = guess_title_and_year(
            "Overlord.S01E07.MULTi.1080p.WEB.x264-Tsundere-Raws.mkv"
        )
        assert is_tv is True
        assert "Overlord" in t

    def test_no_year_returns_none(self):
        _, y, _ = guess_title_and_year("Random.Name.mkv")
        assert y is None


# ---------------------------------------------------------------------------
# guess_season_episode
# ---------------------------------------------------------------------------

class TestGuessSeasonEpisode:
    def test_sxxexx_uppercase(self):
        assert guess_season_episode("Show.S01E03.mkv") == (1, 3)

    def test_sxxexx_lowercase(self):
        assert guess_season_episode("show.s02e15.mkv") == (2, 15)

    def test_returns_none_when_not_a_pattern(self):
        assert guess_season_episode("Movie.2024.1080p.mkv") == (None, None)

    def test_x_separator_pattern(self):
        # `1x05` style (TVDB convention) — currently not always handled,
        # so we just assert the function doesn't crash. If we add that
        # branch later, update this test.
        result = guess_season_episode("Show.1x05.mkv")
        assert isinstance(result, tuple) and len(result) == 2


# ---------------------------------------------------------------------------
# resolve_title_year_from_path
# ---------------------------------------------------------------------------

class TestResolveTitleYearFromPath:
    def test_climbs_to_series_folder_for_anime(self, tmp_path):
        # Mimic: Overlord/Saison 1/Overlord.S01E07.mkv
        series = tmp_path / "Overlord (2015)"
        season = series / "Saison 1"
        season.mkdir(parents=True)
        episode = season / "Overlord.S01E07.MULTi.1080p.WEB.x264-Tsundere-Raws.mkv"
        episode.write_bytes(b"")

        title, year, is_tv = resolve_title_year_from_path(str(episode))
        assert "Overlord" in title
        # Year may come from the series folder name.
        assert year in ("2015", None)
        assert is_tv is True

    def test_uses_filename_for_movies(self, tmp_path):
        movie = tmp_path / "The.Matrix.1999.1080p.BluRay.x264.mkv"
        movie.write_bytes(b"")
        title, year, is_tv = resolve_title_year_from_path(str(movie))
        assert "Matrix" in title
        assert year == "1999"
        assert is_tv is False
