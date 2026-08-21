"""``jarvis connect`` -- manage data source connections."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table


def _connector_state_path(source: str) -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return Path(DEFAULT_CONFIG_DIR) / "connectors" / f"{source}.json"


def _load_connector_state(source: str) -> dict:
    """Return persisted connector config for ``source``, or ``{}`` if none."""
    state_file = _connector_state_path(source)
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except (OSError, ValueError):
        return {}


def _save_connector_state(source: str, data: dict) -> None:
    state_file = _connector_state_path(source)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(data))


def _sync_connector(instance: object) -> int:
    """Run a connector through the shared incremental ingestion pipeline."""
    from openjarvis.connectors.pipeline import IngestionPipeline
    from openjarvis.connectors.store import KnowledgeStore
    from openjarvis.connectors.sync_engine import SyncEngine

    return SyncEngine(IngestionPipeline(KnowledgeStore())).sync(instance)


def _activated_sources_path() -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return Path(DEFAULT_CONFIG_DIR) / "connectors" / "_activated.json"


def _load_activated_sources() -> set[str]:
    """Return the set of connector IDs the user has explicitly run
    ``jarvis connect <source>`` for.

    This is deliberately separate from "has valid credentials" — a single
    Google OAuth consent grants tokens for gmail/gcalendar/gdrive/gcontacts/
    google_tasks all at once (see oauth.py's ``GOOGLE_ALL_SCOPES``), so
    ``is_connected()`` being true for a source doesn't mean the user ever
    asked for that specific source to be active. ``--sync`` must only touch
    sources named here, not everything a shared OAuth grant happens to cover.
    """
    state_file = _activated_sources_path()
    if not state_file.exists():
        return set()
    try:
        data = json.loads(state_file.read_text())
        return set(data.get("sources", []))
    except (OSError, ValueError):
        return set()


def _mark_source_activated(source: str) -> None:
    activated = _load_activated_sources()
    if source in activated:
        return
    activated.add(source)
    state_file = _activated_sources_path()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"sources": sorted(activated)}))


def _instantiate_for_status(key: str, connector_cls: type) -> object:
    """Instantiate a connector the same way for both --list and --sync.

    Filesystem connectors need their persisted path (bare instantiation
    always reports disconnected); everything else reads its own credentials.
    """
    auth_type = getattr(connector_cls, "auth_type", "unknown")
    if auth_type != "filesystem":
        return connector_cls()

    saved_path = _load_connector_state(key).get("path", "")
    if not saved_path:
        return connector_cls()
    try:
        return connector_cls(vault_path=saved_path)
    except TypeError:
        return connector_cls(saved_path)


def _list_sources(registry: object) -> None:
    """Print a Rich table of registered connectors and their sync status."""
    console = Console()
    items = registry.items()  # type: ignore[attr-defined]

    if not items:
        console.print("[yellow]No connectors registered.[/yellow]")
        return

    table = Table(title="Connected Sources")
    table.add_column("Source", style="cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Status", style="green")

    for key, connector_cls in items:
        auth_type = getattr(connector_cls, "auth_type", "unknown")
        try:
            instance = _instantiate_for_status(key, connector_cls)
            connected = instance.is_connected()
            status = "connected" if connected else "disconnected"
        except Exception:  # noqa: BLE001
            status = "unknown"

        table.add_row(key, auth_type, status)

    console.print(table)


def _sync_all(registry: object) -> None:
    """Incrementally re-sync every explicitly-activated source.

    Only sources the user has actually run ``jarvis connect <source>`` for
    are touched — see ``_load_activated_sources`` for why that's not the
    same as "has a valid token." Failures on one source are logged and don't
    stop the others.
    """
    console = Console()
    activated = sorted(_load_activated_sources())

    if not activated:
        console.print(
            "[yellow]No sources have been explicitly connected yet."
            " Run `jarvis connect <source>` first.[/yellow]"
        )
        return

    table = Table(title="Sync Results")
    table.add_column("Source", style="cyan")
    table.add_column("Result", style="green")

    for source in activated:
        if not registry.contains(source):  # type: ignore[attr-defined]
            table.add_row(source, "[red]unknown connector — skipped[/red]")
            continue

        connector_cls = registry.get(source)  # type: ignore[attr-defined]
        try:
            instance = _instantiate_for_status(source, connector_cls)
            if not instance.is_connected():
                table.add_row(source, "[yellow]not connected — skipped[/yellow]")
                continue
            chunks = _sync_connector(instance)
            table.add_row(source, f"indexed {chunks} chunk{'s' if chunks != 1 else ''}")
        except Exception as exc:  # noqa: BLE001
            table.add_row(source, f"[red]failed: {exc}[/red]")

    console.print(table)


def _disconnect_source(registry: object, source: str) -> None:
    """Find and disconnect a registered source connector."""
    console = Console()

    if not registry.contains(source):  # type: ignore[attr-defined]
        console.print(f"[red]Unknown source: {source}[/red]")
        return

    connector_cls = registry.get(source)  # type: ignore[attr-defined]
    try:
        instance = connector_cls()
        instance.disconnect()
        console.print(f"[green]Disconnected {source}.[/green]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to disconnect {source}: {exc}[/red]")


def _connect_source(registry: object, source: str, path: str = "") -> None:
    """Route connector setup by auth_type."""
    console = Console()

    if not registry.contains(source):  # type: ignore[attr-defined]
        console.print(f"[red]Unknown source: {source}[/red]")
        console.print(
            "[yellow]Available sources: "
            + ", ".join(registry.keys())  # type: ignore[attr-defined]
            + "[/yellow]"
        )
        return

    connector_cls = registry.get(source)  # type: ignore[attr-defined]
    auth_type = getattr(connector_cls, "auth_type", "")

    if auth_type == "filesystem":
        # Filesystem connectors (e.g. Obsidian) need a path
        if not path:
            console.print(
                f"[red]{source} requires a --path argument (e.g. --path ~/vault).[/red]"
            )
            return
        try:
            instance = connector_cls(vault_path=path)
        except TypeError:
            try:
                instance = connector_cls(path)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Failed to create {source} connector: {exc}[/red]")
                return

        if instance.is_connected():
            try:
                chunks = _sync_connector(instance)
            except Exception as exc:  # noqa: BLE001
                console.print(
                    f"[red]{source} connected at path: {path}, but ingestion"
                    f" failed: {exc}[/red]"
                )
                return

            _save_connector_state(source, {"path": path})
            _mark_source_activated(source)
            console.print(
                f"[green]{source} connected — indexed {chunks} chunk"
                f"{'s' if chunks != 1 else ''} from {path}.[/green]"
            )
        else:
            console.print(
                f"[red]{source}: path '{path}' does not exist or is not accessible."
                "[/red]"
            )

    elif auth_type == "oauth":
        # OAuth connectors — auto-open browser + catch callback
        from openjarvis.connectors.oauth import (
            get_client_credentials,
            get_provider_for_connector,
            run_connector_oauth,
            save_client_credentials,
        )

        instance = connector_cls()
        already_connected = instance.is_connected()

        if not already_connected:
            try:
                provider = get_provider_for_connector(source)
                if provider is None:
                    console.print(
                        f"[red]No OAuth provider configured for {source}.[/red]"
                    )
                    return

                creds = get_client_credentials(provider)
                client_id = creds[0] if creds else ""
                client_secret = creds[1] if creds else ""

                if not client_id or not client_secret:
                    console.print(f"[cyan]First-time setup for {source}.[/cyan]")
                    console.print(
                        f"[yellow]Create an OAuth app at: {provider.setup_url}[/yellow]"
                    )
                    console.print(f"[dim]{provider.setup_hint}[/dim]")
                    client_id = click.prompt("Client ID")
                    client_secret = click.prompt("Client Secret")
                    save_client_credentials(provider, client_id, client_secret)

                run_connector_oauth(source, client_id, client_secret)
                instance = connector_cls()
                if not instance.is_connected():
                    raise RuntimeError("OAuth completed without a usable access token")
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]OAuth flow failed for {source}: {exc}[/red]")
                return

        try:
            chunks = _sync_connector(instance)
        except Exception as exc:  # noqa: BLE001
            console.print(
                f"[red]{source} is connected, but ingestion failed: {exc}[/red]"
            )
            return

        _mark_source_activated(source)
        action = "connected" if already_connected else "authorised"
        console.print(
            f"[green]{source} {action} — indexed {chunks} chunk"
            f"{'s' if chunks != 1 else ''}.[/green]"
        )

    elif auth_type == "token":
        # Token-based connectors (e.g. Oura) — prompt for personal access token
        import json
        from pathlib import Path

        from openjarvis.connectors.oauth import save_tokens
        from openjarvis.core.config import DEFAULT_CONFIG_DIR

        try:
            instance = connector_cls()
            if instance.is_connected():
                _mark_source_activated(source)
                console.print(f"[green]{source} is already connected.[/green]")
                return

            token = click.prompt(f"Enter your {source} personal access token")
            token_dir = Path(DEFAULT_CONFIG_DIR) / "connectors"
            token_dir.mkdir(parents=True, exist_ok=True)
            token_file = token_dir / f"{source}.json"
            token_file.write_text(json.dumps({"token": token}))
            save_tokens(source, {"token": token})
            _mark_source_activated(source)
            console.print(f"[green]{source} connected successfully.[/green]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Token setup failed for {source}: {exc}[/red]")

    else:
        # Generic / bridge connectors
        try:
            instance = connector_cls()
            connected = instance.is_connected()
            status = "connected" if connected else "disconnected"
            console.print(f"{source} status: {status}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Failed to connect {source}: {exc}[/red]")


@click.group(invoke_without_command=True)
@click.argument("source", required=False)
@click.option(
    "--list",
    "list_sources",
    is_flag=True,
    help="List connected sources and sync status.",
)
@click.option(
    "--sync",
    "trigger_sync",
    is_flag=True,
    help="Trigger incremental sync for all sources.",
)
@click.option(
    "--disconnect",
    "disconnect_source",
    default="",
    help="Disconnect a source.",
)
@click.option(
    "--path",
    default="",
    help="Path for filesystem connectors (e.g., Obsidian vault).",
)
@click.pass_context
def connect(
    ctx: click.Context,
    source: str | None,
    list_sources: bool,
    trigger_sync: bool,
    disconnect_source: str,
    path: str,
) -> None:
    """Manage data source connections (Gmail, Obsidian, etc.)."""
    # Lazy imports to avoid top-level side effects
    import openjarvis.connectors  # noqa: F401 — registers all connectors
    from openjarvis.core.registry import ConnectorRegistry

    if list_sources:
        _list_sources(ConnectorRegistry)
        return

    if trigger_sync:
        _sync_all(ConnectorRegistry)
        return

    if disconnect_source:
        _disconnect_source(ConnectorRegistry, disconnect_source)
        return

    if source:
        _connect_source(ConnectorRegistry, source, path=path)
        return

    # No arguments — show help
    click.echo(ctx.get_help())
