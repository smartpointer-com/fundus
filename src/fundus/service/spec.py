"""Pure description of the launchd jobs Fundus installs — no side effects.

Up to three jobs per install: ``<prefix>.index`` (incremental, every N minutes),
``<prefix>.index-full`` (the nightly rsync-style full reconcile, at a wall-clock
time), and ``<prefix>.serve`` (the long-running read-only MCP server, kept alive).
Each plist runs a small login-shell wrapper that optionally sources a secrets
env-file at runtime and then execs the *installed* ``fundus`` binary, so no secret
ever lands in the world-readable plist. Everything here is a plain data transform
(→ a plist dict) to keep it unit-testable without touching launchd.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["agent", "daemon"]

# A deliberately explicit PATH: a launchd job inherits almost no environment, so name the
# Homebrew + system bin dirs the indexer/server (and any tool they shell out to) might need.
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def parse_hhmm(value: str) -> dict[str, int]:
    """Parse ``"HH:MM"`` into a StartCalendarInterval dict. Raises ValueError if out of range."""
    hh, _, mm = value.strip().partition(":")
    hour, minute = int(hh), int(mm or "0")
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"invalid time {value!r}; expected HH:MM in 00:00–23:59")
    return {"Hour": hour, "Minute": minute}


def fundus_command(fundus_bin: str, config_path: str, env_file: str | None, *args: str) -> list[str]:
    """ProgramArguments: a bash wrapper that sources secrets (if any) then execs ``fundus <args>``."""
    inner = f"exec {shlex.quote(fundus_bin)} {' '.join(args)} --config {shlex.quote(config_path)}"
    if env_file:
        src = shlex.quote(env_file)
        inner = f"set -a; [ -r {src} ] && . {src}; set +a; {inner}"
    return ["/bin/bash", "-c", inner]


@dataclass(frozen=True)
class Job:
    label: str
    program_arguments: list[str]
    environment: dict[str, str]
    log_path: str
    start_interval: int | None = None  # seconds (incremental)
    calendar: dict[str, int] | None = None  # {"Hour", "Minute"} (nightly full)
    run_at_load: bool = False
    keep_alive: bool = False  # long-running server: relaunch whenever it exits
    background: bool = True  # batch jobs run de-prioritized; a responsive server does not
    username: str | None = None  # daemon only — run as this user, not root
    groupname: str | None = None
    working_directory: str | None = None

    def to_plist(self) -> dict[str, Any]:
        plist: dict[str, Any] = {
            "Label": self.label,
            "ProgramArguments": self.program_arguments,
            "EnvironmentVariables": self.environment,
            "StandardOutPath": self.log_path,
            "StandardErrorPath": self.log_path,
        }
        if self.background:  # OS de-prioritizes CPU/IO behind foreground work (index jobs)
            plist["ProcessType"] = "Background"
            plist["LowPriorityIO"] = True
        if self.run_at_load:
            plist["RunAtLoad"] = True
        if self.keep_alive:
            plist["KeepAlive"] = True
        if self.start_interval is not None:
            plist["StartInterval"] = self.start_interval
        if self.calendar is not None:
            plist["StartCalendarInterval"] = self.calendar
        if self.username:
            plist["UserName"] = self.username
        if self.groupname:
            plist["GroupName"] = self.groupname
        if self.working_directory:
            plist["WorkingDirectory"] = self.working_directory
        return plist


@dataclass(frozen=True)
class Plan:
    """Everything needed to render the jobs for one install."""

    kind: Kind
    label_prefix: str
    fundus_bin: str
    config_path: str
    env_file: str | None
    logs_dir: str
    interval_minutes: int
    full_at: str
    home: str
    username: str
    groupname: str
    include_index: bool = True  # the incremental + nightly-full index jobs
    include_serve: bool = True  # the long-running read-only MCP server

    @property
    def incremental_label(self) -> str:
        return f"{self.label_prefix}.index"

    @property
    def full_label(self) -> str:
        return f"{self.label_prefix}.index-full"

    @property
    def serve_label(self) -> str:
        return f"{self.label_prefix}.serve"

    def _common(self) -> dict[str, Any]:
        common: dict[str, Any] = {
            "environment": {"HOME": self.home, "PATH": DEFAULT_PATH},
            "working_directory": self.home,
        }
        if self.kind == "daemon":  # a daemon runs as this user (not root) and needs its group
            common["username"] = self.username
            common["groupname"] = self.groupname
        return common

    def jobs(self) -> list[Job]:
        out: list[Job] = []
        common = self._common()
        if self.include_index:
            out.append(Job(
                label=self.incremental_label,
                program_arguments=fundus_command(self.fundus_bin, self.config_path, self.env_file, "index"),
                log_path=f"{self.logs_dir}/index.log",
                start_interval=self.interval_minutes * 60,
                run_at_load=True,  # catch up after sleep/boot
                **common,
            ))
            out.append(Job(
                label=self.full_label,
                program_arguments=fundus_command(self.fundus_bin, self.config_path, self.env_file, "index", "--full"),
                log_path=f"{self.logs_dir}/index-full.log",
                calendar=parse_hhmm(self.full_at),
                run_at_load=False,  # never kick a heavy reconcile at load
                **common,
            ))
        if self.include_serve:
            out.append(Job(
                label=self.serve_label,
                program_arguments=fundus_command(self.fundus_bin, self.config_path, self.env_file, "serve"),
                log_path=f"{self.logs_dir}/serve.log",
                run_at_load=True,
                keep_alive=True,  # a server: keep it up
                background=False,  # responsive, not throttled like the batch index jobs
                **common,
            ))
        return out
