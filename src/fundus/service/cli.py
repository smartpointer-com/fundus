"""`fundus service …` — install/manage the launchd jobs: indexing, the MCP server, and an
optional bare-metal docling-serve."""

from __future__ import annotations

import getpass
import grp
import os
import sys
from pathlib import Path

import typer

from fundus.config import FundusConfig, default_config_path, load_config
from fundus.service import manager
from fundus.service.spec import (
    DOCLING_SUFFIX,
    FULL_SUFFIX,
    INDEX_SUFFIX,
    SERVE_SUFFIX,
    Kind,
    Plan,
    parse_hhmm,
)

service_app = typer.Typer(
    no_args_is_help=True,
    help="Install/manage the launchd jobs: periodic indexing, the MCP server, and an optional "
    "bare-metal docling-serve.",
)

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to fundus.toml (default: XDG).")


@service_app.callback()
def _guard() -> None:
    if sys.platform != "darwin":
        typer.echo("`fundus service` manages launchd, which is macOS-only.", err=True)
        raise typer.Exit(code=2)


def _kind(daemon: bool | None, prefix: str) -> Kind:
    """Explicit --daemon/--agent wins; otherwise detect what's installed, else default to agent."""
    if daemon is not None:
        return "daemon" if daemon else "agent"
    return manager.detect_kind(prefix, Path.home()) or "agent"


def _config_path(config: Path | None) -> str:
    return str((config or default_config_path()).expanduser().absolute())


def _fail(exc: manager.ServiceError) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=1)


def _prefix(label_prefix: str | None, cfg: FundusConfig) -> str:
    return label_prefix or cfg.service.label_prefix


def _job_label(prefix: str, *, full: bool, serve: bool, docling: bool = False) -> str:
    """Resolve which job a restart/run flag refers to (serve, then docling, then full, else index)."""
    if serve:
        return prefix + SERVE_SUFFIX
    if docling:
        return prefix + DOCLING_SUFFIX
    return prefix + (FULL_SUFFIX if full else INDEX_SUFFIX)


@service_app.command()
def install(
    daemon: bool = typer.Option(
        False,
        "--daemon/--agent",
        help="LaunchDaemon (root via sudo; runs headless at boot) vs LaunchAgent (login session). "
        "A daemon still runs as you; pick it only if its services are up without login.",
    ),
    index: bool = typer.Option(True, "--index/--no-index", help="Install the incremental + nightly-full index jobs."),
    serve: bool = typer.Option(True, "--serve/--no-serve", help="Install the long-running read-only MCP server."),
    docling: bool | None = typer.Option(
        None, "--docling/--no-docling",
        help="Install the keep-alive job for a bare-metal docling-serve "
        "(default: from config [service.docling].enabled).",
    ),
    interval: int | None = typer.Option(None, help="Incremental cadence in minutes."),
    full_at: str | None = typer.Option(None, help="Nightly full-reconcile time, HH:MM."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix (reverse-DNS)."),
    config: Path | None = ConfigOpt,
) -> None:
    """Generate the plists and (re)bootstrap the jobs: incremental + nightly-full index and the
    read-only MCP server (both by default; scope with --no-index / --no-serve), plus an optional
    bare-metal docling-serve when [service.docling] is configured (or --docling)."""
    cfg = load_config(config)
    svc = cfg.service
    include_docling = svc.docling.enabled if docling is None else docling
    # Config asks for docling but supplies no command, and the user didn't explicitly request it on
    # the CLI: skip the docling job with a warning rather than aborting the whole install (index and
    # serve are unrelated and shouldn't be blocked by a half-finished [service.docling]).
    if include_docling and not svc.docling.command and docling is None:
        typer.echo(
            "warning: [service.docling].enabled is set but command is empty; skipping the docling job.",
            err=True,
        )
        include_docling = False
    if not index and not serve and not include_docling:
        typer.echo("nothing to install: index, serve, and docling are all disabled.", err=True)
        raise typer.Exit(code=1)
    try:
        fundus_bin = manager.ensure_installed_binary()
        # Validate the launch command before writing a KeepAlive plist, and take back argv[0]
        # resolved to an absolute path (launchd won't search PATH for a bare command name).
        docling_command = manager.ensure_docling_command(svc.docling.command) if include_docling else []
        plan = Plan(
            kind="daemon" if daemon else "agent",
            label_prefix=label_prefix or svc.label_prefix,
            fundus_bin=str(fundus_bin),
            config_path=_config_path(config),
            logs_dir=str(cfg.logs_dir()),
            interval_minutes=interval or svc.interval_minutes,
            full_at=full_at or svc.full_at,
            home=str(Path.home()),
            username=getpass.getuser(),
            groupname=grp.getgrgid(os.getgid()).gr_name,
            include_index=index,
            include_serve=serve,
            include_docling=include_docling,
            docling_command=docling_command,
            docling_environment=svc.docling.environment,
        )
        parse_hhmm(plan.full_at)  # validate before doing anything
        if plan.kind == "daemon":
            typer.echo("Installing a LaunchDaemon — sudo may prompt for your password.")
        labels = manager.install(plan)
    except manager.ServiceError as exc:
        _fail(exc)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Installed {plan.kind}: {', '.join(labels)}")
    typer.echo(f"  binary:   {fundus_bin}")
    if index:
        typer.echo(f"  index:    every {plan.interval_minutes} min; full nightly at {plan.full_at}")
    if serve:
        s = cfg.serve
        typer.echo(f"  serve:    {s.transport} on {s.host}:{s.port} (kept alive)")
    if include_docling:
        typer.echo(f"  docling:  {' '.join(svc.docling.command)} (kept alive)")
    typer.echo(f"  logs:     {plan.logs_dir}/")
    if cfg.env_file is None:
        # launchd jobs get a near-empty environment; without an env_file fundus can't see secrets.
        typer.echo(
            "  WARNING: no env_file in config — the jobs won't have any secrets (Meili key, serve "
            "token). Set a top-level `env_file` to your secrets file, then reinstall.",
            err=True,
        )


@service_app.command()
def uninstall(
    daemon: bool | None = typer.Option(None, "--daemon/--agent", help="Override autodetected kind."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix."),
    config: Path | None = ConfigOpt,
) -> None:
    """Bootout and remove all installed jobs (index + serve + docling)."""
    cfg = load_config(config)
    prefix = _prefix(label_prefix, cfg)
    kind = _kind(daemon, prefix)
    if kind == "daemon":
        typer.echo("Removing a LaunchDaemon — sudo may prompt for your password.")
    removed = manager.uninstall(prefix, kind, Path.home())
    typer.echo(f"Removed: {', '.join(removed)}" if removed else "Nothing installed to remove.")


@service_app.command()
def status(
    daemon: bool | None = typer.Option(None, "--daemon/--agent", help="Override autodetected kind."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix."),
    config: Path | None = ConfigOpt,
) -> None:
    """Show each installed job's launchd state, last exit code, and run count."""
    cfg = load_config(config)
    prefix = _prefix(label_prefix, cfg)
    kind = _kind(daemon, prefix)
    typer.echo(f"kind: {kind}")
    for label in manager.all_labels(prefix):
        typer.echo(manager.status_text(label, kind))


@service_app.command()
def restart(
    full: bool = typer.Option(False, "--full", help="Target the nightly-full job."),
    serve: bool = typer.Option(False, "--serve", help="Target the MCP server (else an index job)."),
    docling: bool = typer.Option(False, "--docling", help="Target the bare-metal docling-serve job."),
    daemon: bool | None = typer.Option(None, "--daemon/--agent", help="Override autodetected kind."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix."),
    config: Path | None = ConfigOpt,
) -> None:
    """Kill-and-restart a job immediately (kickstart -k) — e.g. the server (or docling) after a
    config change."""
    cfg = load_config(config)
    prefix = _prefix(label_prefix, cfg)
    kind = _kind(daemon, prefix)
    label = _job_label(prefix, full=full, serve=serve, docling=docling)
    manager.kickstart(label, kind, restart=True)
    typer.echo(f"Restarted {label}.")


@service_app.command()
def run(
    full: bool = typer.Option(False, "--full", help="Trigger the nightly-full job (else incremental)."),
    daemon: bool | None = typer.Option(None, "--daemon/--agent", help="Override autodetected kind."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix."),
    config: Path | None = ConfigOpt,
) -> None:
    """Trigger an index job to run now without waiting for its timer (for testing)."""
    cfg = load_config(config)
    prefix = _prefix(label_prefix, cfg)
    kind = _kind(daemon, prefix)
    label = _job_label(prefix, full=full, serve=False)
    manager.kickstart(label, kind, restart=False)
    typer.echo(f"Triggered {label}; tail {cfg.logs_dir()}/ for output.")
