"""Outlook Mail connector — read-only email sync via Microsoft Graph.

Uses OAuth 2.0 tokens stored locally (see :mod:`openjarvis.connectors.oauth`
and :mod:`openjarvis.connectors.microsoft_auth`). All network calls are
isolated in module-level functions (``_outlook_api_*``) to make them
trivially mockable in tests — same shape as :mod:`openjarvis.connectors.gmail`.

Deliberately read-only: this connector only lists and reads messages via
``Mail.Read``. There is no send/delete/move/mark-as-read method anywhere in
this file, on purpose — that scope may be added later, but not implicitly.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.connectors.microsoft_auth import (
    MicrosoftAuthError,
)
from openjarvis.connectors.microsoft_auth import (
    call_with_refresh as _call_with_refresh,
)
from openjarvis.connectors.oauth import (
    delete_tokens,
    load_tokens,
    resolve_microsoft_credentials,
)
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.tools._stubs import ToolSpec

# Re-exported so callers that imported the historical Gmail-style alias
# still resolve to something meaningful.
OutlookAuthError = MicrosoftAuthError

_GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
_DEFAULT_CREDENTIALS_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "outlook.json")

# Fields requested from Graph — keep this narrow; only what Document needs.
_MESSAGE_SELECT = ",".join(
    [
        "id",
        "conversationId",
        "subject",
        "from",
        "toRecipients",
        "ccRecipients",
        "receivedDateTime",
        "bodyPreview",
        "body",
        "webLink",
    ]
)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _months_before(value: datetime, months: int) -> datetime:
    """Return the same wall-clock time *months* earlier."""
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


# ---------------------------------------------------------------------------
# Module-level API functions (easy to patch in tests)
# ---------------------------------------------------------------------------


def _outlook_api_list_messages(
    token: str,
    *,
    next_link: Optional[str] = None,
    since: Optional[datetime] = None,
    top: int = 25,
) -> Dict[str, Any]:
    """Call the Graph ``me/messages`` endpoint (list, one page).

    Parameters
    ----------
    token:
        OAuth access token.
    next_link:
        A full ``@odata.nextLink`` URL from a previous response, used
        as-is to fetch the next page (Graph pagination links already carry
        all query params — none should be re-added).
    since:
        When provided (and *next_link* is not), restricts to messages
        received on/after this timestamp via ``$filter``.
    top:
        Page size, only applied on the first request.

    Returns
    -------
    dict
        Raw API response containing ``value`` (list of messages) and
        optional ``@odata.nextLink``.
    """
    headers = {"Authorization": f"Bearer {token}"}
    if next_link:
        resp = httpx.get(next_link, headers=headers, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    params: Dict[str, str] = {
        "$select": _MESSAGE_SELECT,
        "$orderby": "receivedDateTime desc",
        "$top": str(top),
    }
    if since is not None:
        iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params["$filter"] = f"receivedDateTime ge {iso}"

    resp = httpx.get(
        f"{_GRAPH_API_BASE}/me/messages",
        headers=headers,
        params=params,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _address_of(recipient: Dict[str, Any]) -> str:
    return str((recipient.get("emailAddress") or {}).get("address", "")).lower()


def _addresses_of(recipients: List[Dict[str, Any]]) -> List[str]:
    return [a for a in (_address_of(r) for r in recipients) if a]


def _parse_graph_timestamp(value: str) -> datetime:
    """Parse a Graph ISO-8601 timestamp; fall back to now on failure."""
    if not value:
        return datetime.now()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now()


def _body_text(body: Dict[str, Any], preview: str) -> str:
    """Prefer the full body; Graph returns HTML-or-text per contentType."""
    content = body.get("content", "")
    if not content:
        return preview
    if body.get("contentType", "").lower() == "html":
        # No dedicated HTML stripper here yet — same reasonable fallback as
        # returning the raw preview when only HTML is available. A future
        # pass can reuse gmail.py's _html_to_text if this proves too noisy.
        return preview or content
    return content


# ---------------------------------------------------------------------------
# OutlookConnector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("outlook")
class OutlookConnector(BaseConnector):
    """Read-only connector that syncs mail from Outlook via Microsoft Graph.

    Authentication is handled through Microsoft OAuth 2.0 (``Mail.Read``
    only). Tokens are stored locally in a JSON credentials file.

    Parameters
    ----------
    credentials_path:
        Path to the JSON file where OAuth tokens are stored. Defaults to
        ``~/.openjarvis/connectors/outlook.json``.
    initial_sync_months:
        Calendar-month history window used only when no sync checkpoint
        exists. Defaults to ``connectors.outlook.initial_sync_months``
        (12 months).
    """

    connector_id = "outlook"
    display_name = "Outlook Mail"
    auth_type = "oauth"

    def __init__(
        self,
        credentials_path: str = "",
        *,
        initial_sync_months: Optional[int] = None,
    ) -> None:
        self._credentials_path = resolve_microsoft_credentials(
            credentials_path or _DEFAULT_CREDENTIALS_PATH
        )
        if initial_sync_months is None:
            from openjarvis.core.config import load_config

            initial_sync_months = load_config().connectors.outlook.initial_sync_months
        if initial_sync_months < 1:
            raise ValueError("Outlook initial_sync_months must be at least 1")
        self._initial_sync_months = initial_sync_months
        self._items_synced: int = 0
        self._last_sync: Optional[datetime] = None
        self._last_cursor: Optional[str] = None

    # ------------------------------------------------------------------
    # BaseConnector interface
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Return ``True`` if a credentials file with a valid access token exists."""
        tokens = load_tokens(self._credentials_path)
        if tokens is None:
            return False
        return bool(tokens.get("access_token") or tokens.get("token"))

    def disconnect(self) -> None:
        """Delete the stored credentials file."""
        delete_tokens(self._credentials_path)

    def sync(
        self,
        *,
        since: Optional[datetime] = None,
        cursor: Optional[str] = None,
    ) -> Iterator[Document]:
        """Yield :class:`Document` objects for Outlook messages.

        Parameters
        ----------
        since:
            When provided, only messages received on/after this timestamp
            are returned. When omitted for an initial sync, the configured
            rolling history window is used instead.
        cursor:
            An ``@odata.nextLink`` URL from a previous sync to resume
            pagination.
        """
        tokens = load_tokens(self._credentials_path)
        if not tokens or not (tokens.get("token") or tokens.get("access_token")):
            return

        effective_since = since
        if effective_since is None and not cursor:
            effective_since = _months_before(_utc_now(), self._initial_sync_months)

        next_link: Optional[str] = cursor
        synced = 0

        while True:
            list_resp = _call_with_refresh(
                _outlook_api_list_messages,
                self._credentials_path,
                next_link=next_link,
                since=None if next_link else effective_since,
            )
            messages: List[Dict[str, Any]] = list_resp.get("value", [])

            for msg in messages:
                msg_id: str = msg.get("id", "")
                if not msg_id:
                    continue

                from_addr = _address_of(msg.get("from") or {})
                to_addrs = _addresses_of(msg.get("toRecipients", []))
                cc_addrs = _addresses_of(msg.get("ccRecipients", []))
                participants = [a for a in ([from_addr] + to_addrs + cc_addrs) if a]

                body_text = _body_text(
                    msg.get("body") or {}, msg.get("bodyPreview", "")
                )

                doc = Document(
                    doc_id=f"outlook:{msg_id}",
                    source="outlook",
                    source_id=msg_id,
                    doc_type="email",
                    content=body_text,
                    title=msg.get("subject", ""),
                    author=from_addr,
                    participants=participants,
                    timestamp=_parse_graph_timestamp(msg.get("receivedDateTime", "")),
                    thread_id=msg.get("conversationId"),
                    url=msg.get("webLink"),
                    metadata={
                        "message_id": msg_id,
                    },
                )
                synced += 1
                yield doc

            next_link = list_resp.get("@odata.nextLink")
            self._last_cursor = next_link
            if not next_link:
                break

        self._items_synced = synced
        self._last_sync = datetime.now()

    def sync_status(self) -> SyncStatus:
        """Return sync progress from the most recent :meth:`sync` call."""
        return SyncStatus(
            state="idle",
            items_synced=self._items_synced,
            last_sync=self._last_sync,
            cursor=self._last_cursor,
        )

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    def mcp_tools(self) -> List[ToolSpec]:
        """Expose one MCP tool spec for real-time Outlook mail search."""
        return [
            ToolSpec(
                name="outlook_search_emails",
                description=(
                    "Search Outlook mail. Returns matching messages with"
                    " subject, sender, and a body preview."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search text (subject or body).",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of emails to return",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
                category="communication",
            ),
        ]


__all__ = ["OutlookConnector", "OutlookAuthError"]
