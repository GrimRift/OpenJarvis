"""Two-turn confirmation.

The gate is the user turn, not the model. These tests care most about the case
where the model tries to approve its own call — including when it is faithfully
repeating instructions it read in a web page or a document, which is the whole
reason the mechanism exists ahead of M32's on-screen reading.
"""

from __future__ import annotations

import pytest

from openjarvis.security import confirmations


@pytest.fixture(autouse=True)
def _clean():
    confirmations.clear()
    yield
    confirmations.clear()


def _msgs(*pairs):
    return [{"role": role, "content": text} for role, text in pairs]


TOOL = "git_commit"
ARGS = {"message": "wip"}


class TestTheModelCannotApproveItself:
    def test_a_first_ask_is_never_approved(self):
        confirmations.set_turn(_msgs(("user", "commit this")))
        assert confirmations.decide(TOOL, ARGS) is False

    def test_asking_twice_in_one_turn_does_not_approve(self):
        """The loop can call a tool repeatedly; that must not count as consent."""
        confirmations.set_turn(_msgs(("user", "commit this")))
        assert confirmations.decide(TOOL, ARGS) is False
        assert confirmations.decide(TOOL, ARGS) is False
        assert confirmations.decide(TOOL, ARGS) is False

    def test_a_yes_the_user_never_said_does_not_approve(self):
        """An affirmative in the *assistant's* text is not the user's."""
        confirmations.set_turn(_msgs(("user", "commit this")))
        confirmations.decide(TOOL, ARGS)
        confirmations.set_turn(
            _msgs(("user", "commit this"), ("assistant", "Yes, I will commit."))
        )
        assert confirmations.decide(TOOL, ARGS) is False

    def test_no_bound_turn_fails_closed(self):
        """A scheduled job has nobody present to answer."""
        confirmations.clear()
        assert confirmations.decide(TOOL, ARGS) is False


class TestAGenuineYesApproves:
    def test_the_next_turn_saying_yes_approves(self):
        confirmations.set_turn(_msgs(("user", "commit this")))
        assert confirmations.decide(TOOL, ARGS) is False

        confirmations.set_turn(
            _msgs(
                ("user", "commit this"),
                ("assistant", "This will commit with message 'wip'. Confirm?"),
                ("user", "yes"),
            )
        )
        assert confirmations.decide(TOOL, ARGS) is True

    def test_approval_is_spent_once(self):
        """One yes authorises one run, not a standing permission."""
        confirmations.set_turn(_msgs(("user", "commit this")))
        confirmations.decide(TOOL, ARGS)
        confirmed = _msgs(
            ("user", "commit this"),
            ("assistant", "Confirm?"),
            ("user", "yes"),
        )
        confirmations.set_turn(confirmed)
        assert confirmations.decide(TOOL, ARGS) is True
        assert confirmations.decide(TOOL, ARGS) is False


class TestConsentDoesNotTransfer:
    def test_yes_authorises_only_the_call_that_was_asked_about(self):
        """Agreeing to one action must not run a different one in the same turn."""
        other_args = {"message": "rm"}
        confirmations.set_turn(_msgs(("user", "commit this")))
        confirmations.decide(TOOL, ARGS)

        confirmations.set_turn(
            _msgs(("user", "commit this"), ("assistant", "Confirm?"), ("user", "yes"))
        )
        assert confirmations.decide(TOOL, other_args) is False
        assert confirmations.decide(TOOL, ARGS) is True

    def test_a_different_tool_is_not_covered(self):
        confirmations.set_turn(_msgs(("user", "do it")))
        confirmations.decide(TOOL, ARGS)
        confirmations.set_turn(
            _msgs(("user", "do it"), ("assistant", "Confirm?"), ("user", "yes"))
        )
        assert confirmations.decide("create_calendar_event", {}) is False


class TestWhatCountsAsYes:
    @pytest.mark.parametrize(
        "reply",
        [
            "yes",
            "Yes.",
            "confirm",
            "go ahead",
            "do it",
            "proceed",
            "approved",
            # Reported too strict in live use: a fixed phrase list rejected
            # ordinary padded agreement and cost an extra exchange each time.
            "yep sure thing",
            "ok go ahead please",
            "sounds good",
            "please do it",
            "yes sir",
            "sure go ahead",
            "alright",
            "absolutely",
            "that sounds good",
        ],
    )
    def test_plain_agreement(self, reply):
        assert confirmations.is_affirmative(reply) is True

    @pytest.mark.parametrize(
        "reply",
        [
            "no",
            "not yet",
            "yes, but change the date first",
            "why do you need confirmation?",
            "did you say yes?",
            "",
            "commit this and say yes to any prompts",
            # Widening the vocabulary must not let a correction through.
            "yes and delete the old one",
            "ok but use 5pm",
            "yes to the second one only",
            "sure, delete the first event",
            "do the other one",
            "good morning",
            "no thanks",
        ],
    )
    def test_anything_short_of_plain_agreement(self, reply):
        """A security decision read out of prose only accepts an unambiguous yes.

        "yes, but change the date first" is the dangerous one: it reads as
        agreement and is actually a correction.
        """
        assert confirmations.is_affirmative(reply) is False


class TestTurnIdentity:
    def test_a_new_user_message_changes_the_turn(self):
        first = confirmations.turn_key(_msgs(("user", "commit this")))
        second = confirmations.turn_key(
            _msgs(("user", "commit this"), ("assistant", "Confirm?"), ("user", "yes"))
        )
        assert first != second

    def test_the_same_exchange_is_the_same_turn(self):
        a = confirmations.turn_key(_msgs(("user", "commit this")))
        b = confirmations.turn_key(_msgs(("user", "commit this")))
        assert a == b

    def test_it_reads_objects_as_well_as_dicts(self):
        """The route passes pydantic messages, tests pass dicts."""

        class _M:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        assert confirmations.turn_key([_M("user", "hi")]) == confirmations.turn_key(
            _msgs(("user", "hi"))
        )


class TestExecutorIntegration:
    """The executor must ask, not fail.

    requires_confirmation=True used to return "requires confirmation but no
    confirmation callback is available" in the chat path, which disabled the
    tool outright — git_commit sat in the configured tool list and could never
    run. The result now has to be something the model can relay.
    """

    def _executor(self):
        from openjarvis.core.types import ToolResult
        from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec

        class _Guarded(BaseTool):
            calls = 0

            @property
            def spec(self):
                return ToolSpec(
                    name="git_commit",
                    description="Commit",
                    parameters={"type": "object", "properties": {}},
                    requires_confirmation=True,
                )

            def execute(self, **params):
                type(self).calls += 1
                return ToolResult(
                    tool_name="git_commit", content="committed", success=True
                )

        tool = _Guarded()
        type(tool).calls = 0
        return ToolExecutor(tools=[tool]), tool

    def _call(self):
        from openjarvis.core.types import ToolCall

        return ToolCall(id="c1", name="git_commit", arguments="{}")

    def test_the_first_attempt_asks_instead_of_running(self):
        executor, tool = self._executor()
        confirmations.set_turn(_msgs(("user", "commit this")))

        result = executor.execute(self._call())

        assert result.success is False
        assert type(tool).calls == 0
        assert "Confirmation required" in result.content
        assert result.metadata.get("requires_confirmation") is True

    def test_it_no_longer_claims_confirmation_is_impossible(self):
        """The old message told the model to give up; this one tells it to ask."""
        executor, _ = self._executor()
        confirmations.set_turn(_msgs(("user", "commit this")))
        result = executor.execute(self._call())
        assert "no confirmation callback" not in result.content

    def test_the_second_turn_with_a_yes_runs_it(self):
        executor, tool = self._executor()
        confirmations.set_turn(_msgs(("user", "commit this")))
        executor.execute(self._call())

        confirmations.set_turn(
            _msgs(("user", "commit this"), ("assistant", "Confirm?"), ("user", "yes"))
        )
        result = executor.execute(self._call())

        assert result.success is True
        assert type(tool).calls == 1

    def test_an_explicit_callback_still_wins(self):
        """The CLI's --yes auto-approval must keep working."""
        executor, tool = self._executor()
        executor._interactive = True
        executor._confirm_callback = lambda _prompt: True
        result = executor.execute(self._call())
        assert result.success is True
        assert type(tool).calls == 1


class TestFingerprintIsCanonical:
    """The model re-sends the same call as fresh JSON.

    Hashing the rendered prompt string made key order significant, so an
    identical request looked new and the user was asked to confirm twice —
    caught on the first live run of this feature.
    """

    def test_key_order_does_not_change_the_call(self):
        a = confirmations.fingerprint("t", {"summary": "x", "start": "y"})
        b = confirmations.fingerprint("t", {"start": "y", "summary": "x"})
        assert a == b

    def test_a_different_value_is_a_different_call(self):
        a = confirmations.fingerprint("t", {"summary": "x"})
        b = confirmations.fingerprint("t", {"summary": "z"})
        assert a != b

    def test_a_different_tool_is_a_different_call(self):
        assert confirmations.fingerprint("a", {}) != confirmations.fingerprint("b", {})

    def test_unserialisable_arguments_do_not_raise(self):
        assert confirmations.fingerprint("t", {"o": object()})

    def test_reordered_arguments_still_redeem(self):
        confirmations.set_turn(_msgs(("user", "add it")))
        assert confirmations.decide("t", {"summary": "x", "start": "y"}) is False
        confirmations.set_turn(
            _msgs(("user", "add it"), ("assistant", "Confirm?"), ("user", "yes"))
        )
        assert confirmations.decide("t", {"start": "y", "summary": "x"}) is True
