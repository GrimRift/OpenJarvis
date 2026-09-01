"""What must not be repeated back off the screen.

The user's rule: no special handling in general, except passwords and banking.

Not hypothetical — the first window enumeration run on the real machine
returned a Notepad window whose *title was a credential*. Listing windows is
enough to leak it; nobody has to open the file.
"""

from __future__ import annotations

import pytest

from openjarvis.security.screen_redaction import (
    REDACTED,
    is_sensitive_title,
    redact_title,
)


class TestPasswordsAndBanking:
    @pytest.mark.parametrize(
        "title",
        [
            "1Password",
            "Bitwarden - Vault",
            "KeePass 2.x",
            "Sign in - Dashlane",
            "Windows Credential Manager",
            "Microsoft Authenticator",
        ],
    )
    def test_password_managers_are_redacted(self, title):
        assert is_sensitive_title(title) is True

    @pytest.mark.parametrize(
        "title",
        [
            "Online Banking - Log in",
            "BDO Internet Banking",
            "Funds Transfer",
            "Credit Card Statement - March",
        ],
    )
    def test_banking_is_redacted(self, title):
        assert is_sensitive_title(title) is True


class TestCredentialsInTitles:
    """A title that *is* a secret is password info, whatever app shows it."""

    @pytest.mark.parametrize(
        "title",
        [
            "sk-abcdefghijklmnopqrstuvwxyz123456",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "AKIAIOSFODNN7EXAMPLE",
            # The shape actually seen on screen: a mixed-case token as the
            # filename, shown in the window title by the editor.
            "*LAb8Q7P9Gzp1v6IIZF7g16g4PhNXDLggpw - Notepad",
        ],
    )
    def test_token_shaped_titles_are_redacted(self, title):
        assert is_sensitive_title(title) is True


class TestOrdinaryTitlesSurvive:
    """Over-redaction is its own failure: Sage goes blind and cannot say why."""

    @pytest.mark.parametrize(
        "title",
        [
            "Sage - Opera",
            "Claude",
            "HANDOFF.md - OpenJarvis-Lab - Visual Studio Code",
            "Program Manager",
            "*Implement through shared code, chec - Notepad",
            "Inbox (12) - Gmail",
            "C:\\AI\\OpenJarvis-Lab\\src\\openjarvis\\tools",
            "report-2026-09-01-final.pdf",
        ],
    )
    def test_normal_windows_are_left_alone(self, title):
        assert is_sensitive_title(title) is False
        assert redact_title(title) == title

    def test_an_empty_title_is_not_sensitive(self):
        assert is_sensitive_title("") is False


class TestRedactionOutput:
    def test_a_sensitive_title_is_replaced_wholesale(self):
        assert redact_title("1Password") == REDACTED

    def test_the_secret_never_appears_in_the_replacement(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        assert secret not in redact_title(secret)
