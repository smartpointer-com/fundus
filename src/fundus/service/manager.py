"""Side-effecting half of `fundus service`: validate the binary, write plists, drive launchctl.

The pure job/plist description lives in ``spec.py``; this module turns a ``Plan`` into
loaded launchd services. Daemon operations shell out through ``sudo`` (writing into
``/Library/LaunchDaemons`` and bootstrapping the ``system`` domain need root) with the
terminal attached, so the password prompt comes through normally.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fundus.service.spec import Job, Kind, Plan

DAEMON_DIR = Path("/Library/LaunchDaemons")


class ServiceError(RuntimeError):
    """A user-facing problem (bad binary, launchctl failure) — printed without a traceback."""


# --- Binary validation: a service must point at an INSTALLED, matching fundus ------------------


def self_binary() -> Path:
    """Absolute path of the running ``fundus`` console script (what a plist would reference)."""
    exe = sys.argv[0]
    if os.sep in exe:
        return Path(exe).absolute()
    return Path(shutil.which(exe) or exe).absolute()


def is_editable_install() -> bool:
    """True if the running fundus is an editable/dev install (depends on the source tree)."""
    try:
        raw = importlib.metadata.distribution("fundus").read_text("direct_url.json")
        if not raw:
            return False
        return bool(json.loads(raw).get("dir_info", {}).get("editable"))
    except Exception:  # noqa: BLE001 - absence of metadata just means "can't prove editable"
        return False


def _pipx_hint() -> str | None:
    for cand in (Path.home() / ".local/bin/fundus", Path.home() / ".local/pipx/venvs/fundus/bin/fundus"):
        if cand.exists():
            return str(cand)
    return None


def ensure_installed_binary() -> Path:
    """Resolve the fundus binary to embed, refusing a dev build (the user must `make install`).

    The plist points at the binary that invoked ``service install``; if that binary is the editable
    dev build, the generated job would depend on the source tree and drift from any installed copy —
    exactly the mismatch to prevent — so we stop and tell the user to install and re-invoke.
    """
    exe = self_binary()
    if is_editable_install():
        hint = _pipx_hint()
        tail = f" (e.g. {hint})" if hint else ""
        raise ServiceError(
            f"This is the editable dev build at {exe}.\n"
            f"A launchd service must point at an installed binary, or it will break on any repo "
            f"change.\nRun `make install`, then run `fundus service install` via the installed "
            f"CLI{tail}."
        )
    if not exe.exists():
        raise ServiceError(f"Cannot resolve a fundus binary at {exe}. Run `make install` first.")
    return exe


# --- launchctl targets ------------------------------------------------------------------------


def domain_target(kind: Kind, uid: int) -> str:
    return "system" if kind == "daemon" else f"gui/{uid}"


def plist_path(kind: Kind, label: str, home: Path) -> Path:
    base = DAEMON_DIR if kind == "daemon" else home / "Library" / "LaunchAgents"
    return base / f"{label}.plist"


def all_labels(label_prefix: str) -> list[str]:
    """Every label fundus may install: the two index jobs and the serve daemon."""
    return [f"{label_prefix}.index", f"{label_prefix}.index-full", f"{label_prefix}.serve"]


def detect_kind(label_prefix: str, home: Path) -> Kind | None:
    """Which kind is currently installed (by which plist exists), or None. Checks every label,
    since an install may be index-only or serve-only."""
    for label in all_labels(label_prefix):
        if plist_path("daemon", label, home).exists():
            return "daemon"
        if plist_path("agent", label, home).exists():
            return "agent"
    return None


def _run(cmd: list[str], *, sudo: bool, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    argv = (["sudo", *cmd]) if sudo else cmd
    return subprocess.run(argv, check=check, text=True, capture_output=capture)


# --- Operations -------------------------------------------------------------------------------


def _write_plist(job: Job, kind: Kind, home: Path) -> Path:
    dest = plist_path(kind, job.label, home)
    data = plistlib.dumps(job.to_plist())
    if kind == "daemon":
        with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            # Daemon plists must be root:wheel 644 or launchd refuses to load them.
            _run(["install", "-m", "644", "-o", "root", "-g", "wheel", tmp_path, str(dest)], sudo=True)
        finally:
            os.unlink(tmp_path)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return dest


def install(plan: Plan) -> list[str]:
    """Write both plists and (re)bootstrap them. Returns the labels installed."""
    Path(plan.logs_dir).mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    kind = plan.kind
    sudo = kind == "daemon"
    labels: list[str] = []
    for job in plan.jobs():
        dest = _write_plist(job, kind, Path(plan.home))
        target = f"{domain_target(kind, uid)}/{job.label}"
        # Idempotent: bootout any previous incarnation first, then bootstrap. capture=True swallows
        # the expected "No such process" on a first install (nothing loaded to boot out).
        _run(["launchctl", "bootout", target], sudo=sudo, check=False, capture=True)
        _run(["launchctl", "bootstrap", domain_target(kind, uid), str(dest)], sudo=sudo)
        labels.append(job.label)
    return labels


def uninstall(label_prefix: str, kind: Kind, home: Path) -> list[str]:
    uid = os.getuid()
    sudo = kind == "daemon"
    removed: list[str] = []
    for label in all_labels(label_prefix):
        _run(["launchctl", "bootout", f"{domain_target(kind, uid)}/{label}"],
             sudo=sudo, check=False, capture=True)
        dest = plist_path(kind, label, home)
        if dest.exists():
            if kind == "daemon":
                _run(["rm", "-f", str(dest)], sudo=True)
            else:
                dest.unlink()
            removed.append(label)
    return removed


def kickstart(label: str, kind: Kind, *, restart: bool) -> None:
    """Trigger a job now (``-k`` to kill-and-restart an in-flight one)."""
    uid = os.getuid()
    flag = ["-k"] if restart else []
    _run(["launchctl", "kickstart", *flag, f"{domain_target(kind, uid)}/{label}"], sudo=kind == "daemon")


def status_text(label: str, kind: Kind) -> str:
    """`launchctl print` output for a job, or a clear 'not loaded' line."""
    uid = os.getuid()
    res = _run(
        ["launchctl", "print", f"{domain_target(kind, uid)}/{label}"],
        sudo=kind == "daemon",
        check=False,
        capture=True,
    )
    if res.returncode != 0:
        return f"{label}: not loaded"
    keep = ("state =", "pid =", "last exit code =", "runs =")
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip().startswith(keep)]
    return f"{label}:\n  " + "\n  ".join(lines) if lines else f"{label}: loaded"
