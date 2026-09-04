from __future__ import annotations

import re
from typing import List, Tuple

#: Parameter names whose *value* is a credential regardless of what the value
#: looks like. Matching by position rather than by shape is the point: the
#: patterns below this list only catch tokens with a recognisable prefix, and
#: most keys have none. An OpenWeatherMap key is 32 hex characters and nothing
#: else -- it reached a tool result, the model's context and the logs through
#: an httpx error quoting the request URL, and no shape-based rule here would
#: ever have matched it.
_SECRET_QUERY_PARAMS = (
    "api_?key|apikey|appid|app_?id|auth|access_?token|refresh_?token|"
    "client_?secret|id_?token|key|passwd|password|pwd|secret|session|sig|"
    "signature|token"
)

_CREDENTIAL_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    # Value of a credential-named query parameter, in a URL anywhere in the
    # text. Stops at the next separator so the rest of the URL survives and
    # the message stays diagnosable.
    (
        "query_param",
        re.compile(
            rf"([?&](?:{_SECRET_QUERY_PARAMS})=)[^&\s'\"<>)\]]+",
            re.IGNORECASE,
        ),
    ),
    # Telegram puts the bot token in the URL path, not a parameter, so no
    # query rule can catch it: https://api.telegram.org/bot<id>:<secret>/...
    ("telegram_token", re.compile(r"(/bot)\d+:[A-Za-z0-9_-]{20,}")),
    ("api_key", re.compile(r"sk-[a-zA-Z0-9_-]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
    ("github_token", re.compile(r"gho_[a-zA-Z0-9]{36}")),
    ("slack_token", re.compile(r"xoxb-[0-9A-Za-z\-]+")),
    ("bearer_token", re.compile(r"Bearer\s+[a-zA-Z0-9_\-.]{20,}")),
]


class CredentialStripper:
    """Redacts credentials from text using compiled regex patterns."""

    def __init__(self) -> None:
        self._patterns = _CREDENTIAL_PATTERNS

    def strip(self, text: str) -> str:
        for label, pattern in self._patterns:
            replacement = (
                rf"\g<1>[REDACTED:{label}]"
                if pattern.groups
                else f"[REDACTED:{label}]"
            )
            text = pattern.sub(replacement, text)
        return text


def wrap_tool_output(tool_name: str, content: str, success: bool = True) -> str:
    status = "success" if success else "error"
    header = f'<tool_result name="{tool_name}" status="{status}">'
    return f"{header}\n{content}\n</tool_result>"
