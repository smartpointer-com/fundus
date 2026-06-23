"""`fundus service …` — install/manage the launchd indexing jobs."""

from __future__ import annotations

import getpass
import grp
import os
import sys
from pathlib import Path

import typer

from fundus.config import FundusConfig, default_config_path, load_config
from fundus.service import manager
from fundus.service.spec import Kind, Plan, parse_hhmm

service_app = typer.Typer(no_args_is_help=True, help="Install/manage periodic indexing (launchd).")

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


@service_app.command()
def install(
    daemon: bool = typer.Option(
        False,
        "--daemon/--agent",
        help="LaunchDaemon (root via sudo; runs headless at boot) vs LaunchAgent (login session). "
        "A daemon still runs as you; pick it only if its services are up without login.",
    ),
    interval: int | None = typer.Option(None, help="Incremental cadence in minutes."),
    full_at: str | None = typer.Option(None, help="Nightly full-reconcile time, HH:MM."),
    env_file: str | None = typer.Option(None, help="File sourced for secrets at runtime."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix (reverse-DNS)."),
    config: Path | None = ConfigOpt,
) -> None:
    """Generate the plists and (re)bootstrap the incremental + nightly-full jobs."""
    cfg = load_config(config)
    svc = cfg.service
    try:
        fundus_bin = manager.ensure_installed_binary()
        ef = env_file if env_file is not None else svc.env_file
        plan = Plan(
            kind="daemon" if daemon else "agent",
            label_prefix=label_prefix or svc.label_prefix,
            fundus_bin=str(fundus_bin),
            config_path=_config_path(config),
            env_file=str(Path(ef).expanduser()) if ef else None,
            logs_dir=str(cfg.logs_dir()),
            interval_minutes=interval or svc.interval_minutes,
            full_at=full_at or svc.full_at,
            home=str(Path.home()),
            username=getpass.getuser(),
            groupname=grp.getgrgid(os.getgid()).gr_name,
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
    typer.echo(f"  every:    {plan.interval_minutes} min (incremental); full nightly at {plan.full_at}")
    typer.echo(f"  logs:     {plan.logs_dir}/")


def _prefix(label_prefix: str | None, cfg: FundusConfig) -> str:
    return label_prefix or cfg.service.label_prefix


@service_app.command()
def uninstall(
    daemon: bool | None = typer.Option(None, "--daemon/--agent", help="Override autodetected kind."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix."),
    config: Path | None = ConfigOpt,
) -> None:
    """Bootout and remove both jobs."""
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
    """Show each job's launchd state, last exit code, and run count."""
    cfg = load_config(config)
    prefix = _prefix(label_prefix, cfg)
    kind = _kind(daemon, prefix)
    typer.echo(f"kind: {kind}")
    for label in (f"{prefix}.index", f"{prefix}.index-full"):
        typer.echo(manager.status_text(label, kind))


@service_app.command()
def restart(
    full: bool = typer.Option(False, "--full", help="Restart the nightly-full job (else incremental)."),
    daemon: bool | None = typer.Option(None, "--daemon/--agent", help="Override autodetected kind."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix."),
    config: Path | None = ConfigOpt,
) -> None:
    """Kill-and-restart a job immediately (kickstart -k)."""
    cfg = load_config(config)
    prefix = _prefix(label_prefix, cfg)
    kind = _kind(daemon, prefix)
    label = f"{prefix}.index-full" if full else f"{prefix}.index"
    manager.kickstart(label, kind, restart=True)
    typer.echo(f"Restarted {label}.")


@service_app.command()
def run(
    full: bool = typer.Option(False, "--full", help="Trigger the nightly-full job (else incremental)."),
    daemon: bool | None = typer.Option(None, "--daemon/--agent", help="Override autodetected kind."),
    label_prefix: str | None = typer.Option(None, help="Job label prefix."),
    config: Path | None = ConfigOpt,
) -> None:
    """Trigger a job to run now without waiting for its timer (for testing)."""
    cfg = load_config(config)
    prefix = _prefix(label_prefix, cfg)
    kind = _kind(daemon, prefix)
    label = f"{prefix}.index-full" if full else f"{prefix}.index"
    manager.kickstart(label, kind, restart=False)
    typer.echo(f"Triggered {label}; tail {cfg.logs_dir()}/ for output.")
