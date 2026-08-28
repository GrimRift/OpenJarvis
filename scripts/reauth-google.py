"""Force a fresh Google OAuth consent, preserving stored client credentials.

Google refresh tokens issued by an app still in *Testing* publishing status
expire after seven days. When that happens every Google connector starts
failing with ``invalid_grant — Token has been expired or revoked``, and the
proactive agent reports "Nothing to report" because it fetched nothing.

``jarvis connect gmail`` cannot repair this: it skips the OAuth flow whenever
a token file exists (``connect_cmd.py``'s ``if not already_connected``), and
an expired refresh token still leaves that file in place — so it goes
straight to a sync that fails. Deleting the file would force consent but also
discards the stored client_id/client_secret, which live in the same JSON.

This calls ``run_connector_oauth`` directly, which resolves the stored client
credentials itself and overwrites only the tokens. One consent covers every
Google connector, since they share a single grant (``GOOGLE_ALL_SCOPES``).

    .venv\\Scripts\\python.exe scripts\\reauth-google.py
"""

from __future__ import annotations

import sys

from openjarvis.connectors.oauth import OAUTH_PROVIDERS, run_connector_oauth

# Every Google connector shares one grant, so re-authing through any of them
# refreshes all of them. gmail is used because it is the one always set up.
_CONNECTOR = "gmail"


def main() -> int:
    provider = OAUTH_PROVIDERS.get("google")
    if provider is None:
        print("No Google OAuth provider configured.")
        return 1

    print("Requesting Google consent for scopes:")
    for scope in provider.scopes:
        print(f"  - {scope}")
    print("\nA browser window will open. Approve the permissions there.\n")

    try:
        run_connector_oauth(_CONNECTOR)
    except Exception as exc:  # noqa: BLE001
        print(f"OAuth failed: {exc}")
        return 1

    print("\nGoogle re-authenticated. Run 'jarvis connect --sync' to catch up.")
    print(
        "\nNote: if the OAuth app is still in Testing status, this refresh "
        "token expires again in 7 days. Publishing the app stops that."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
