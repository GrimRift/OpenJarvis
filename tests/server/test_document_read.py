"""Tests for the tiered document reader.

The thresholds here are not taste. They were measured against five real
documents, and every number in these tests is one of those measurements.
"""

from __future__ import annotations

from unittest.mock import patch

from openjarvis.server import document_read
from openjarvis.server.document_read import PageQuality, assemble, assess, assess_page

# Real extractor output from a two-column journal paper: table rows spliced
# into body prose, subscripts orphaned, spacing gone.
MANGLED = (
    "FeO 11.0 7.1 3.5 12.41 4.20 concrete construction ranging from 10 to 50%\n"
    "NaO 2.41 3.8 0.5 3.12 0.10\n"
    "2 stratedbyadropindensityandincreasedwaterabsorption\n"
    "[58].ThespecificgravityofVAis1.98[48],whichislessthan\n"
    "thespecificgravityofcement.Thereplacementoflowspe-\n"
) * 4

CLEAN = (
    "Mount Apo is the highest mountain in the Philippines, standing 2,954 "
    "metres above sea level on the island of Mindanao. The climb is popular "
    "with hikers throughout the year, and several trails lead to the summit.\n"
) * 6

# Pages 8-14 of a real lab report: long numeric tokens, no glued prose. These
# are legitimate data tables and must NOT be re-read.
TABULAR = (
    "Specimen CE231C_LAB04_S01 1.2345678901 2.3456789012 3.4567890123\n"
    "Specimen CE231C_LAB04_S02 4.5678901234 5.6789012345 6.7890123456\n"
) * 8


class TestItSpotsMangledText:
    def test_glued_prose_is_flagged(self):
        page = assess_page(0, MANGLED)
        assert page.mangled is True
        assert page.needs_eyes is True

    def test_ordinary_prose_is_not(self):
        page = assess_page(0, CLEAN)
        assert page.mangled is False
        assert page.needs_eyes is False

    def test_a_table_of_numbers_is_not_mangled(self):
        """Mean word length was measured at 15-26 on real tabular pages.

        Using it as a trigger sent 12 clean pages of a 22-page lab report to
        the vision model. Long tokens are not glued prose.
        """
        page = assess_page(0, TABULAR)
        assert page.mean_word > 8.0, "the trap only exists if these are long"
        assert page.glued_per_1k == 0.0
        assert page.needs_eyes is False


class TestScanningIsJudgedAcrossTheDocument:
    def test_a_sparse_page_in_a_healthy_document_is_left_alone(self):
        """A near-empty page is a figure or a divider, not a scan.

        Judging this per page flagged 48 pages of a 2,061-page novel.
        """
        pages = assess([CLEAN, CLEAN, "Figure 4.", CLEAN])
        assert [p.index for p in pages if p.needs_eyes] == []

    def test_a_document_with_no_text_layer_is_flagged_whole(self):
        pages = assess(["", "  ", "3"])
        assert all(p.scanned for p in pages)
        assert len([p for p in pages if p.needs_eyes]) == 3

    def test_no_pages_is_not_a_crash(self):
        assert assess([]) == []


class TestOnlyBadPagesArePaidFor:
    def _pages(self):
        return assess([CLEAN, MANGLED, CLEAN, MANGLED])

    def test_a_clean_document_never_calls_the_model(self):
        with patch.object(document_read, "render_pages") as render:
            replacements, note = document_read.read_pages_with_vision(
                b"", assess([CLEAN, CLEAN])
            )
        assert replacements == {}
        assert note == ""
        assert not render.called

    def test_only_the_mangled_pages_are_rendered(self):
        with patch.object(document_read, "render_pages", return_value=[]) as render:
            document_read.read_pages_with_vision(b"", self._pages())
        assert render.call_args.args[1] == [1, 3]

    def test_the_page_cap_is_honoured_and_reported(self):
        pages = assess([MANGLED] * 25)
        with patch.object(document_read, "render_pages", return_value=[]) as render:
            _replacements, note = document_read.read_pages_with_vision(
                b"", pages, max_pages=20
            )
        assert len(render.call_args.args[1]) == 20
        assert "5 further page(s)" in note

    def test_a_failed_render_says_so_rather_than_returning_nothing(self):
        """Silence would read as "the document had nothing in it"."""
        with patch.object(document_read, "render_pages", side_effect=OSError("boom")):
            replacements, note = document_read.read_pages_with_vision(
                b"", self._pages()
            )
        assert replacements == {}
        assert "garbled" in note


class TestAssembly:
    def test_a_reread_page_replaces_the_mangled_one(self):
        pages = [
            PageQuality(0, "first", 0.0, 5.0),
            PageQuality(1, "GARBLED", 9.0, 20.0),
        ]
        assert assemble(pages, {1: "recovered"}) == "first\n\nrecovered"

    def test_without_replacements_the_original_survives(self):
        pages = [PageQuality(0, "only page", 0.0, 5.0)]
        assert assemble(pages) == "only page"
