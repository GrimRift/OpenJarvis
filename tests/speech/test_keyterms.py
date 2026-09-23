"""What Deepgram is told to expect.

Unboosted, Flux wrote "addition" for *Hey Sage*, "Close to the diagram" for
*close the diagram*, and "in case on those" for *Quezon Province*. Boosting is
the lever; these tests pin that the lever cannot be broken by a long user
list, and that boosting stays narrow enough not to make things worse.
"""

from __future__ import annotations

import json

from openjarvis.speech import keyterms
from openjarvis.speech.flux import KEYTERMS, build_url


def _url(terms):
    return build_url(
        model="flux-general-en",
        eot_threshold=0.7,
        eager_eot_threshold=None,
        eot_timeout_ms=5000,
        keyterms=terms,
    )


class TestTheListIsCleaned:
    def test_short_and_repeated_words_are_dropped(self):
        # A boosted two-letter word turns up everywhere, so "po" and "at"
        # are exactly what must not go in.
        assert keyterms.clean(["po", "at", "Laguna", "laguna", " Quezon "]) == [
            "Laguna",
            "Quezon",
        ]

    def test_whitespace_inside_a_phrase_is_normalised(self):
        assert keyterms.clean(["close   the\tdiagram"]) == ["close the diagram"]

    def test_a_phrase_counts_its_letters_not_its_spaces(self):
        assert keyterms.clean(["a b"]) == []
        assert keyterms.clean(["Hey Sage"]) == ["Hey Sage"]


class TestTheNameSurvivesEverything:
    def test_a_user_list_that_fills_the_cap_cannot_evict_it(self):
        crowd = [f"word{i}" for i in range(keyterms.MAX_TERMS * 3)]
        assert "keyterm=Sage" in _url(crowd)

    def test_no_list_at_all_still_boosts_the_name(self):
        for term in KEYTERMS:
            assert term.replace(" ", "+") in _url(None) or term in _url(None)


class TestReadingTheClassSchedule:
    def test_instructors_and_subject_words_are_picked_up(self, tmp_path):
        schedule = tmp_path / "Class Schedule.md"
        schedule.write_text(
            "| Subject Code | Subject Description | Section | Day | Time | "
            "Room | Mode | Instructor |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| CECMPM1D | Construction Methods and Drafting | BSCE231E | "
            "Tuesday | 05:00PM | Mezz 6 | In-person | Ferly Ann R. Revilloza, MS |\n",
            encoding="utf-8",
        )
        found = keyterms.from_class_schedule(schedule)
        # The surname is what gets mangled; the degree suffix is not a word.
        assert "Revilloza" in found
        assert "Construction" in found
        assert not any(term in ("MS", "and") for term in found)

    def test_a_missing_schedule_is_not_an_error(self, tmp_path):
        assert keyterms.from_class_schedule(tmp_path / "nope.md") == []


class TestTheStoredList:
    def test_it_round_trips_and_is_cleaned_on_the_way_in(self, tmp_path):
        saved = keyterms.save_user_terms(["Revilloza", "po", "Revilloza"], tmp_path)
        assert saved == ["Revilloza"]
        assert keyterms.load_user_terms(tmp_path) == ["Revilloza"]

    def test_a_missing_or_broken_file_reads_as_empty(self, tmp_path):
        assert keyterms.load_user_terms(tmp_path) == []
        (tmp_path / "keyterms.json").write_text("not json", encoding="utf-8")
        assert keyterms.load_user_terms(tmp_path) == []

    def test_the_whole_list_is_capped(self, tmp_path):
        keyterms.save_user_terms([f"word{i}" for i in range(200)], tmp_path)
        assert len(keyterms.all_terms(tmp_path)) <= keyterms.MAX_TERMS


class TestLearnedFromMemory:
    """Names the user talks about are already in memory; Deepgram should
    expect them (24 September: a friend's words came back wrong)."""

    def _facts(self, tmp_path, *facts):
        lines = [json.dumps(fact) for fact in facts]
        (tmp_path / "memory_facts.jsonl").write_text("\n".join(lines), encoding="utf-8")

    def test_names_come_from_what_sage_remembers(self, tmp_path):
        self._facts(
            tmp_path,
            {"text": "Mark watches Kurzgesagt and works in AutoCAD and Revit."},
            {"text": "He is reading Lord of the Mysteries this term."},
        )
        learned = keyterms.from_memory(tmp_path)
        assert {"Kurzgesagt", "AutoCAD", "Revit", "Lord of the Mysteries"} <= set(
            learned
        )
        assert "Lord" not in learned

    def test_ordinary_words_are_not_names(self, tmp_path):
        # "Project" is also written "project"; the first word of a sentence
        # and the days of the week are capitalised anyway.
        self._facts(
            tmp_path,
            {"text": "Mark has a Project due. The project is late."},
            {"text": "Classes are on Mondays with Revilloza."},
        )
        learned = keyterms.from_memory(tmp_path)
        assert "Project" not in learned and "Mark" not in learned
        assert "Mondays" not in learned
        assert "Revilloza" in learned

    def test_private_and_removed_facts_are_never_sent(self, tmp_path):
        self._facts(
            tmp_path,
            {"text": "He banks with Metrobank.", "private": True},
            {"text": "He used Figma once.", "removed_at": 1790000000},
        )
        assert keyterms.from_memory(tmp_path) == []

    def test_names_already_boosted_do_not_use_up_the_slots(self, tmp_path):
        self._facts(tmp_path, {"text": "He studies in Calamba and plays Steam games."})
        learned = keyterms.from_memory(tmp_path, known=list(keyterms.BUILT_IN))
        assert "Calamba" not in learned and "Steam" in learned
