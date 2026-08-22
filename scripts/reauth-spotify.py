"""Force a fresh Spotify OAuth consent, preserving stored client credentials.

``jarvis connect spotify`` skips the OAuth flow whenever a token file already
exists (``connect_cmd.py``'s ``if not already_connected``), so it cannot be
used to pick up newly added scopes — it just re-syncs. Deleting the token
file would force consent but also discards the stored client_id/client_secret
(they live in the same ``spotify.json``), making the user re-enter them.

This calls ``run_connector_oauth`` directly instead: it resolves the stored
client credentials itself and overwrites the token file with a fresh grant,
so only the access/refresh tokens change.

Run after changing ``OAUTH_PROVIDERS["spotify"].scopes``:

    .venv\\Scripts\\python.exe scripts\\reauth-spotify.py
"""

from __future__ import annotations

import sys

from openjarvis.connectors.oauth import OAUTH_PROVIDERS, run_connector_oauth


def main() -> int:
    scopes = OAUTH_PROVIDERS["spotify"].scopes
    print("Requesting Spotify consent for scopes:")
    for scope in scopes:
        print(f"  - {scope}")
    print("\nA browser window will open. Approve the permissions there.\n")

    try:
        run_connector_oauth("spotify")
    except Exception as exc:  # noqa: BLE001
        print(f"OAuth failed: {exc}")
        return 1

    print("\nSpotify re-authenticated. Playback control is now authorised.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
