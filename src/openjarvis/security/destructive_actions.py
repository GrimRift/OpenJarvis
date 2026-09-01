"""Which control names deserve a second look before Sage presses them.

The user's rule: free rein on ordinary controls, confirm anything that looks
destructive. This is the single place that decides, because three action tools
need the same judgement and three copies of a list like this drift apart.

**This is a word list, and word lists are not complete.** "Delete" is caught;
a button labelled "Tidy up", "Reset workspace" or a bare icon with no name is
not. It raises the cost of the common mistakes; it is not a safety boundary,
and nothing here should be described to the user as one.

Matched on whole words, so "Undelete" and "Sendero" do not trip it, and
"Send" in "Send later" does.
"""

from __future__ import annotations

import re

#: Grouped by what goes wrong, which is also how to decide whether to add one.
_DESTRUCTIVE_TERMS = (
    # Data loss
    "delete", "remove", "erase", "wipe", "clear", "discard", "destroy",
    "purge", "trash", "empty", "reset", "format", "uninstall", "drop",
    "revert", "overwrite", "truncate",
    # Reaches other people, and cannot be taken back
    "send", "publish", "post", "share", "submit", "reply", "forward",
    "invite", "broadcast",
    # Money
    "buy", "purchase", "pay", "checkout", "order", "subscribe", "renew",
    "transfer", "withdraw", "donate", "confirm order", "place order",
    # Access and identity
    "revoke", "deactivate", "disable", "unsubscribe", "sign out", "log out",
    "logout", "unlink", "disconnect", "leave",
    # The machine itself
    "shut down", "shutdown", "restart", "reboot", "log off", "power off",
    "end task", "kill",
    # Closing a window throws away unsaved work. Added after Sage closed a
    # Notepad window with two modified tabs, unconfirmed, on the second live
    # test of this feature: "close" was not on the list, so the guard never
    # ran. An extra confirmation on dismissing a dialog is a far smaller cost
    # than losing what someone was in the middle of writing.
    "close", "exit", "quit", "discard", "don't save", "do not save",
)

#: Phrases first so "confirm order" is not merely "order".
_PATTERNS = tuple(
    re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
    for term in sorted(_DESTRUCTIVE_TERMS, key=len, reverse=True)
)

#: Named exceptions. "Save" is not destructive; "Save As" is not either. These
#: exist because the terms above are substrings of ordinary, safe labels.
_SAFE_PHRASES = (
    "clear filter",
    "clear search",
    "clear formatting",
    "remove filter",
    "reset zoom",
    "reset view",
)


def looks_destructive(label: str) -> bool:
    """Whether pressing something with this name warrants a confirmation."""
    if not label:
        # An unnamed control cannot be judged, and pressing a thing you cannot
        # name is exactly when a second look is worth most.
        return True
    lowered = label.lower()
    if any(phrase in lowered for phrase in _SAFE_PHRASES):
        return False
    return any(pattern.search(label) for pattern in _PATTERNS)


def describe_reason(label: str) -> str:
    """Why this needs confirming, in words worth showing the user."""
    if not label:
        return "the control has no readable name"
    hit = next((p.pattern for p in _PATTERNS if p.search(label)), "")
    word = re.sub(r"\(\?<!\\w\)|\(\?!\\w\)|\\", "", hit)
    return f"it is named {label!r}" + (f" and contains {word!r}" if word else "")


__all__ = ["describe_reason", "looks_destructive"]
