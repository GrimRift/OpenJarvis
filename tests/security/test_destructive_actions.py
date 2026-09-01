"""Which control names get a confirmation before Sage presses them.

The user's rule: free rein on ordinary controls, confirm anything destructive.

This is a word list and the tests say so. It raises the cost of the common
mistakes; it is not a safety boundary, and an unusual label — "Tidy up",
"Reset workspace", a bare icon — walks straight through. The over-matching
tests matter as much as the under-matching ones: a guard that fires on Save
and Copy is a guard the user turns off.
"""

from __future__ import annotations

import pytest

from openjarvis.security.destructive_actions import describe_reason, looks_destructive


class TestItCatchesTheObviousOnes:
    @pytest.mark.parametrize(
        "label",
        [
            "Delete",
            "Delete Account",
            "Remove item",
            "Empty Trash",
            "Format Disk",
            "Uninstall",
            "Reset",
        ],
    )
    def test_data_loss(self, label):
        assert looks_destructive(label) is True

    @pytest.mark.parametrize(
        "label", ["Send", "Send later", "Publish", "Post", "Submit", "Reply"]
    )
    def test_things_that_reach_other_people(self, label):
        assert looks_destructive(label) is True

    @pytest.mark.parametrize(
        "label", ["Buy now", "Purchase", "Place order", "Confirm order", "Pay"]
    )
    def test_money(self, label):
        assert looks_destructive(label) is True

    @pytest.mark.parametrize("label", ["Shut down", "Restart", "Sign out", "Log off"])
    def test_the_machine_itself(self, label):
        assert looks_destructive(label) is True

    @pytest.mark.parametrize(
        "label",
        ["Close", "Close Tab", "Exit", "Quit", "Discard", "Close without saving"],
    )
    def test_closing_a_window(self, label):
        """Added after Sage closed a Notepad window with two modified tabs,
        unconfirmed, on the second live test: "close" was not on the list, so
        the guard never ran. An extra confirmation on dismissing a dialog costs
        far less than losing what someone was writing."""
        assert looks_destructive(label) is True


class TestItLeavesOrdinaryControlsAlone:
    """A guard that fires on Save is a guard that gets switched off."""

    @pytest.mark.parametrize(
        "label",
        [
            "Save",
            "Save As",
            "Open",
            "Copy",
            "Paste",
            "Search",
            "Bold (Ctrl+B)",
            "Add New Tab",
            "File",
            "Edit",
            "View",
            "Cancel",
            "OK",
        ],
    )
    def test_safe_labels(self, label):
        assert looks_destructive(label) is False

    @pytest.mark.parametrize("label", ["Clear filter", "Clear search", "Reset zoom"])
    def test_named_exceptions_survive(self, label):
        """These contain a trigger word but undo nothing that matters."""
        assert looks_destructive(label) is False

    @pytest.mark.parametrize("label", ["Undelete", "Sendero", "Resend rate"])
    def test_it_matches_whole_words(self, label):
        assert looks_destructive(label) is False


class TestTheUnknownIsTreatedAsRisky:
    def test_an_unnamed_control_is_confirmed(self):
        """Pressing a thing you cannot name is when a second look is worth most."""
        assert looks_destructive("") is True


class TestTheReasonIsWorthShowing:
    def test_it_names_the_control(self):
        assert "Delete Account" in describe_reason("Delete Account")

    def test_an_unnamed_control_says_so(self):
        assert "no readable name" in describe_reason("")
