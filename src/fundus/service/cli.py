"""`fundus service …` — install/manage the launchd jobs: indexing + the MCP server."""

from __future__ import annotations

import getpass
import grp
import os
import sys
from pathlib import Path

import typer

from fundus.config import FundusConfig, default_config_path, load_config
from fundus.service import manager
from fundus.service.spec import FULL_SUFFIX, INDEX_SUFFIX, SERVE_SUFFIX, Kind, Plan, parse_hhmm

service_app = typer.Typer(
    no_args_is_help=True, help="Install/manage the launchd jobs: periodic indexing + the MCP server."
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


def _job_label(prefix: str, *, full: bool, serve: bool) -> str:
    """Resolve which job a restart/run/target flag refers to (serve wins, then full, else index)."""
    if serve:
        return prefix + SERVE_SUFFIX
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
    interval: int | None = typer.Option(None, help="Incremental cadence in minutes."),
    full_at: str | None = typer.Option(None, help="Nightly full-reconcile time, HH:MM."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix (reverse-DNS)."),
    config: Path | None = ConfigOpt,
) -> None:
    """Generate the plists and (re)bootstrap the jobs: incremental + nightly-full index, and the
    read-only MCP server (all by default; scope with --no-index / --no-serve)."""
    cfg = load_config(config)
    svc = cfg.service
    if not index and not serve:
        typer.echo("nothing to install: --no-index and --no-serve can't both be set.", err=True)
        raise typer.Exit(code=1)
    try:
        fundus_bin = manager.ensure_installed_binary()
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
    """Bootout and remove all installed jobs (index + serve)."""
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
    daemon: bool | None = typer.Option(None, "--daemon/--agent", help="Override autodetected kind."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix."),
    config: Path | None = ConfigOpt,
) -> None:
    """Kill-and-restart a job immediately (kickstart -k) — e.g. the server after a config change."""
    cfg = load_config(config)
    prefix = _prefix(label_prefix, cfg)
    kind = _kind(daemon, prefix)
    label = _job_label(prefix, full=full, serve=serve)
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
