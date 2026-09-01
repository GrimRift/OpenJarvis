"""Deciding what on screen must not be repeated back.

The user's rule: no special handling in general, *except* passwords and banking
— redact those. This module is the single place that decides, because screen
capture will need exactly the same judgement as window listing, and two copies
of a rule like this drift.

Not hypothetical. The first window enumeration run on this machine returned a
Notepad window whose *title was itself a credential* — the filename was the
secret. Nobody has to open the file for that to leak; listing the windows is
enough.
"""

from __future__ import annotations

import re

REDACTED = "[redacted — looks sensitive]"

#: Applications whose window titles routinely name the account being unlocked.
_SENSITIVE_APPS = (
    "1password",
    "bitwarden",
    "dashlane",
    "keepass",
    "keeper",
    "lastpass",
    "nordpass",
    "proton pass",
    "credential manager",
    "keychain",
    "authenticator",
)

#: Banking is named rather than enumerated: a list of banks is endless and
#: stale the day it is written, so match what banking windows say about
#: themselves.
_BANKING_TERMS = (
    "online banking",
    "internet banking",
    "mobile banking",
    "bank of",
    "banking",
    "e-banking",
    "netbanking",
    "account summary",
    "wire transfer",
    "funds transfer",
    "credit card statement",
)

#: Well-known key shapes. A title carrying one of these is a secret outright.
_KEY_PREFIXES = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,})"
)

#: A long unbroken run mixing cases and digits is a token, not a sentence.
_TOKEN_RUN = re.compile(r"[A-Za-z0-9+/_~-]{24,}")


def _looks_like_token(text: str) -> bool:
    """Whether *text* contains something shaped like a secret.

    Deliberately narrow: real words and file paths must survive. The test is a
    long run that mixes upper case, lower case *and* digits, which ordinary
    prose and filenames do not do.
    """
    if _KEY_PREFIXES.search(text):
        return True
    for run in _TOKEN_RUN.findall(text):
        if (
            any(c.isupper() for c in run)
            and any(c.islower() for c in run)
            and any(c.isdigit() for c in run)
        ):
            return True
    return False


def is_sensitive_title(title: str) -> bool:
    """Whether a window title should be withheld."""
    if not title:
        return False
    lowered = title.lower()
    if any(app in lowered for app in _SENSITIVE_APPS):
        return True
    if any(term in lowered for term in _BANKING_TERMS):
        return True
    return _looks_like_token(title)


def redact_title(title: str) -> str:
    """The title as it may be reported."""
    return REDACTED if is_sensitive_title(title) else title


__all__ = ["REDACTED", "is_sensitive_title", "redact_title"]
