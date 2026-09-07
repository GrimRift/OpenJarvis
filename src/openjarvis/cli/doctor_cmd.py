"""``jarvis doctor`` — run diagnostic checks on the OpenJarvis installation.

The checks themselves live in :mod:`openjarvis.core.health` so that this
command, the Health page and the ``system_health`` tool cannot drift apart.
This module is the terminal rendering of that one shared run.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import click
from rich.console import Console
from rich.table import Table

from openjarvis.core.health import (
    CheckResult,
    _check_config_exists,
    _check_config_parses,
    _check_default_model,
    _check_engines,
    _check_models,
    _check_nodejs,
    _check_optional_deps,
    _check_python_version,
    _check_security_profile,
    _check_speech_backend,
    results_to_dicts,
    run_health_checks,
)

__all__ = [
    "CheckResult",
    "_check_config_exists",
    "_check_config_parses",
    "_check_default_model",
    "_check_engines",
    "_check_models",
    "_check_nodejs",
    "_check_optional_deps",
    "_check_python_version",
    "_check_security_profile",
    "_check_speech_backend",
    "doctor",
]

# -- Main command -------------------------------------------------------------

_STATUS_ICONS = {
    "ok": "[green]✓[/green]",
    "warn": "[yellow]![/yellow]",
    "fail": "[red]✗[/red]",
}


def _run_all_checks(live: bool = False) -> List[CheckResult]:
    """Run all diagnostic checks and return results."""
    return run_health_checks(live=live).checks


def _results_to_dicts(checks: List[CheckResult]) -> List[Dict[str, Any]]:
    """Convert CheckResult list to JSON-serializable dicts."""
    return results_to_dicts(checks)


@click.command()
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON.")
@click.option(
    "--live",
    is_flag=True,
    help="Also probe paid or quota-limited providers over the network.",
)
def doctor(as_json: bool, live: bool) -> None:
    """Run diagnostic checks on your OpenJarvis installation."""
    checks = _run_all_checks(live=live)

    if as_json:
        click.echo(json.dumps(_results_to_dicts(checks), indent=2))
        return

    console = Console()
    console.print()
    console.print("[bold]OpenJarvis Doctor[/bold]")
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", width=3, justify="center")
    table.add_column("Check")
    table.add_column("Result")

    for check in checks:
        icon = _STATUS_ICONS.get(check.status, "?")
        message = check.message
        if check.details:
            message += f"\n  [dim]{check.details}[/dim]"
        table.add_row(icon, check.name, message)

    console.print(table)

    ok_count = sum(1 for c in checks if c.status == "ok")
    warn_count = sum(1 for c in checks if c.status == "warn")
    fail_count = sum(1 for c in checks if c.status == "fail")
    console.print()
    console.print(f"  {ok_count} passed, {warn_count} warnings, {fail_count} failures")
    console.print()

    # Background tasks section
    from openjarvis.cli._bg_state import get_status
    from openjarvis.core.paths import get_config_dir

    scripts_dir = get_config_dir() / ".scripts"
    console.print("[bold]Background tasks[/bold]")
    bg = get_status()
    bg_failed = False

    if bg.rust_extension == "ready":
        console.print("  [green]✓[/green] Rust extension: ready")
    elif bg.rust_extension == "failed":
        console.print(f"  [red]✗[/red] Rust extension: failed — {bg.rust_error[:80]}")
        console.print(
            f"    retry: {scripts_dir}/install-rust.sh && "
            f"{scripts_dir}/build-extension.sh"
        )
        bg_failed = True
    else:
        console.print(
            "  [yellow]…[/yellow] Rust extension: building (run in background)"
        )

    if not bg.models:
        console.print("  [dim]no model downloads tracked[/dim]")
    for model_id, state in bg.models.items():
        if state == "ready":
            console.print(f"  [green]✓[/green] {model_id}: ready")
        elif state == "failed":
            console.print(f"  [red]✗[/red] {model_id}: failed")
            console.print(f"    retry: {scripts_dir}/pull-model.sh {model_id}")
            bg_failed = True
        else:
            console.print(f"  [yellow]…[/yellow] {model_id}: downloading")

    if bg_failed:
        raise click.exceptions.Exit(code=1)
