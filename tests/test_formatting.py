"""
Unit tests for the formatting and parsing helpers in NFO_generator.

These tests lock down the behaviours that previously regressed (box-width
overflows, HDR parsing, aspect-ratio snapping) so they can't silently break
again. They have no external dependencies beyond pytest — they don't touch
libmediainfo or ffmpeg.

Run with:
    pytest
"""

import sys
from pathlib import Path

# Make the project root importable when pytest is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from nfo_generator import (  # noqa: E402
    BOX_INNER_WIDTH,
    build_hdr_warning_note,
    closest_standard_ratio,
    format_description,
    format_links_section,
    format_two_columns,
    get_language_full_name,
    normalize_date,
    render_ascii_banner,
    wrap_text,
)

# The fully assembled box is 75 characters wide: 1 border + 73 inner + 1 border.
EXPECTED_ROW_WIDTH = BOX_INNER_WIDTH + 2  # == 75


# ---------------------------------------------------------------------------
# wrap_text
# ---------------------------------------------------------------------------

class TestWrapText:
    def test_short_text_is_not_wrapped(self):
        assert wrap_text("hello world", width=80) == ["hello world"]

    def test_wraps_on_spaces(self):
        assert wrap_text("the quick brown fox", width=10) == ["the quick", "brown fox"]

    def test_wraps_long_dotted_filename_on_dots(self):
        name = "Project.Hail.Mary.2026.MULTi.VF2.iMAX.2160p.DV.HDR.WEB-DL.H265-BUC"
        lines = wrap_text(name, width=40)
        # Every produced line must respect the width budget...
        assert all(len(line) <= 40 for line in lines)
        # ...and the break should land on a delimiter, not mid-token, so no
        # line should start with a stray fragment like "UC" from "BUC".
        assert len(lines) >= 2

    def test_hard_cut_when_no_delimiter(self):
        lines = wrap_text("a" * 50, width=20)
        assert lines[0] == "a" * 20


# ---------------------------------------------------------------------------
# Box-width invariants — every rendered row must be exactly 75 chars.
# ---------------------------------------------------------------------------

class TestBoxWidths:
    def test_two_columns_normal(self):
        row = format_two_columns(
            "CODEC..........:", "x264 - WEB-DL",
            "BiTRATE........:", "8000 Kb/s",
        )
        assert len(row) == EXPECTED_ROW_WIDTH
        assert row.startswith("█") and row.endswith("█")

    def test_two_columns_long_left_value_no_collision(self):
        # Regression: "English (United States)" used to glue onto "CHANNELS".
        row = format_two_columns(
            "LANGUAGE.......:", "English (United States)",
            "CHANNELS.......:", "5.1",
        )
        assert len(row) == EXPECTED_ROW_WIDTH
        # The two columns must be separated by at least two spaces.
        assert "States)  " in row or "States) " in row
        assert "States)CHANNELS" not in row

    def test_description_width(self):
        for line in format_description("A short synopsis of the film.").split("\n"):
            assert len(line) == EXPECTED_ROW_WIDTH, f"bad width: {line!r}"

    def test_description_na(self):
        for line in format_description("N/A").split("\n"):
            assert len(line) == EXPECTED_ROW_WIDTH

    def test_description_wraps_long_text(self):
        long_text = "word " * 60  # definitely longer than one line
        lines = format_description(long_text.strip()).split("\n")
        assert len(lines) > 1
        assert all(len(line) == EXPECTED_ROW_WIDTH for line in lines)


# ---------------------------------------------------------------------------
# Aspect ratio snapping
# ---------------------------------------------------------------------------

class TestAspectRatio:
    def test_snaps_to_2_39_1(self):
        assert closest_standard_ratio(3840 / 1608) == "2.39:1"

    def test_snaps_to_1_85_1(self):
        # Regression: 3840x2076 used to render as the absurd "172:93".
        assert closest_standard_ratio(3840 / 2076) == "1.85:1"

    def test_snaps_to_16_9(self):
        assert closest_standard_ratio(16 / 9) == "16:9"

    def test_unknown_ratio_falls_back_to_small_fraction(self):
        result = closest_standard_ratio(1.234)
        num, _, denom = result.partition(":")
        assert denom and int(denom) <= 20  # limit_denominator(20)


# ---------------------------------------------------------------------------
# HDR warning note
# ---------------------------------------------------------------------------

class TestHDRWarning:
    def test_none_when_no_hdr(self):
        assert build_hdr_warning_note(None) is None
        assert build_hdr_warning_note("") is None

    def test_dolby_vision_only(self):
        note = build_hdr_warning_note("Dolby Vision, Version 1.0, Profile 8.1")
        assert note and "Dolby Vision" in note
        assert "compatible" in note.lower()

    def test_hdr10_only(self):
        note = build_hdr_warning_note("SMPTE ST 2086, HDR10")
        assert note and "HDR10" in note

    def test_dv_plus_hdr10_plus(self):
        note = build_hdr_warning_note(
            "Dolby Vision, Version 1.0 / SMPTE ST 2094 App 4, HDR10+ Profile B"
        )
        assert note and "Dolby Vision" in note and "HDR10+" in note


# ---------------------------------------------------------------------------
# Language names
# ---------------------------------------------------------------------------

class TestLanguageNames:
    def test_region_uses_short_code(self):
        assert get_language_full_name("en-US") == "English (US)"

    def test_no_region(self):
        assert get_language_full_name("fr") == "French"

    def test_empty_and_none(self):
        assert get_language_full_name(None) == "Unknown"
        assert get_language_full_name("") == "Unknown"


# ---------------------------------------------------------------------------
# Date normalization
# ---------------------------------------------------------------------------

class TestNormalizeDate:
    def test_iso_to_ddmmyyyy(self):
        assert normalize_date("2025-01-25") == "25-01-2025"

    def test_with_time_and_utc(self):
        assert normalize_date("2025-08-26 11:47:52 UTC") == "26-08-2025"

    def test_garbage_returns_na(self):
        assert normalize_date("not a date") == "N/A"
        assert normalize_date(None) == "N/A"


# ---------------------------------------------------------------------------
# Links section
# ---------------------------------------------------------------------------

class TestLinksSection:
    def test_empty_links_returns_empty_string(self):
        assert format_links_section({}) == ""
        assert format_links_section({"TMDB...........:": ""}) == ""

    def test_populated_links_render_rows(self):
        section = format_links_section({
            "TMDB...........:": "https://www.themoviedb.org/movie/1",
            "iMDB...........:": "https://www.imdb.com/title/tt1",
        })
        assert "LiNKS" in section
        assert "themoviedb" in section
        # Every line in the section must keep the 75-wide box invariant.
        for line in section.strip("\n").split("\n"):
            assert len(line) == EXPECTED_ROW_WIDTH, f"bad width: {line!r}"


# ---------------------------------------------------------------------------
# ASCII banner — graceful behaviour without/with pyfiglet
# ---------------------------------------------------------------------------

class TestAsciiBanner:
    def test_empty_text_returns_empty(self):
        assert render_ascii_banner("") == ""
        assert render_ascii_banner("   ") == ""

    def test_banner_rows_fit_box(self):
        banner = render_ascii_banner("TEST")
        # Whether rendered by pyfiglet or the fallback, every line must fit
        # the 75-wide box and have no blank leading/trailing rows.
        lines = banner.split("\n")
        assert lines  # non-empty
        assert lines[0].strip("█ ")  # first row isn't blank padding
        assert lines[-1].strip("█ ")  # last row isn't blank padding
        for line in lines:
            assert len(line) == EXPECTED_ROW_WIDTH


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
