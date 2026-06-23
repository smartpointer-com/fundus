"""Pure description of the launchd jobs Fundus installs — no side effects.

Two jobs per install: ``<prefix>.index`` (incremental, every N minutes) and
``<prefix>.index-full`` (the nightly rsync-style full reconcile, at a wall-clock
time). The plist runs a small login-shell wrapper that optionally sources a
secrets env-file at runtime and then execs the *installed* ``fundus`` binary, so
no secret ever lands in the world-readable plist. Everything here is a plain data
transform (→ a plist dict) to keep it unit-testable without touching launchd.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["agent", "daemon"]

# A deliberately explicit PATH: a launchd job inherits almost no environment, so name the
# Homebrew + system bin dirs the indexer (and any tool it shells out to) might need.
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def parse_hhmm(value: str) -> dict[str, int]:
    """Parse ``"HH:MM"`` into a StartCalendarInterval dict. Raises ValueError if out of range."""
    hh, _, mm = value.strip().partition(":")
    hour, minute = int(hh), int(mm or "0")
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"invalid time {value!r}; expected HH:MM in 00:00–23:59")
    return {"Hour": hour, "Minute": minute}


def index_command(fundus_bin: str, config_path: str, env_file: str | None, *, full: bool) -> list[str]:
    """The ProgramArguments: a bash wrapper that sources secrets (if any) then execs fundus."""
    inner = f"exec {shlex.quote(fundus_bin)} index"
    if full:
        inner += " --full"
    inner += f" --config {shlex.quote(config_path)}"
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
            "ProcessType": "Background",  # OS de-prioritizes CPU/IO behind foreground work
            "LowPriorityIO": True,
        }
        if self.run_at_load:
            plist["RunAtLoad"] = True
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
    """Everything needed to render the two jobs for one install."""

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

    @property
    def incremental_label(self) -> str:
        return f"{self.label_prefix}.index"

    @property
    def full_label(self) -> str:
        return f"{self.label_prefix}.index-full"

    def jobs(self) -> list[Job]:
        env = {"HOME": self.home, "PATH": DEFAULT_PATH}
        common: dict[str, Any] = {"environment": env, "working_directory": self.home}
        if self.kind == "daemon":  # a daemon runs as this user (not root) and needs its group
            common["username"] = self.username
            common["groupname"] = self.groupname
        return [
            Job(
                label=self.incremental_label,
                program_arguments=index_command(
                    self.fundus_bin, self.config_path, self.env_file, full=False
                ),
                log_path=f"{self.logs_dir}/index.log",
                start_interval=self.interval_minutes * 60,
                run_at_load=True,  # catch up after sleep/boot
                **common,
            ),
            Job(
                label=self.full_label,
                program_arguments=index_command(
                    self.fundus_bin, self.config_path, self.env_file, full=True
                ),
                log_path=f"{self.logs_dir}/index-full.log",
                calendar=parse_hhmm(self.full_at),
                run_at_load=False,  # never kick a heavy reconcile at load
                **common,
            ),
        ]
