"""On-demand lifecycle for extraction engines.

An engine configured with a ``start`` command is raised lazily — on the first
extraction request that actually reaches it, not at process startup — and torn
down when the run ends. Heavyweight engines (a bare-metal docling-serve, a tika
container) then consume memory only while extraction is happening; a run that
brings no new documents starts nothing at all. The query path never touches
this: search stays warm regardless.

Ownership rule: an engine that is already reachable when first needed was
started by something else and is left exactly as found — only what this module
started gets stopped. That makes the feature safe to leave configured next to
an always-on deployment, a manual server, or the bakeoff.

Two modes, chosen by the config shape:

- ``start`` only — a managed child process: spawned directly (output to
  ``engine-<name>.log`` under the logs dir), health-polled until it answers
  HTTP, terminated at run end.
- ``start`` + ``stop`` — delegated commands: both are run to completion (e.g.
  ``docker compose start/stop <service>``), with the same health polling.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, NoReturn

import httpx
import structlog

from fundus.config import EngineConfig
from fundus.extract.base import Extractor, ExtractRequest
from fundus.models import ExtractionResult

log = structlog.get_logger("fundus.extract.lifecycle")

_PROBE_TIMEOUT = 2.0  # per HTTP probe
_PROBE_INTERVAL = 0.25  # between probes while waiting for startup
_STOP_GRACE = 10.0  # SIGTERM -> SIGKILL escalation for managed children
_MAX_STARTS = 3  # per run; a crash-looping engine fails the run, not the host


class EngineStartError(RuntimeError):
    """The engine could not be started (or died) and extraction cannot proceed."""


def _reachable(url: str) -> bool:
    """Whether anything HTTP is answering at ``url`` — any status counts, only a
    transport-level failure (nothing listening, connection refused/reset) does not."""
    try:
        httpx.get(url, timeout=_PROBE_TIMEOUT)
    except httpx.TransportError:
        return False
    return True


class EngineLifecycle:
    """Start/stop state for one engine. ``ensure`` is thread-safe and idempotent, so the
    indexing worker pool can call it on every request; only the first one pays.

    ``probe`` overrides the HTTP reachability check (tests only)."""

    def __init__(
        self,
        name: str,
        cfg: EngineConfig,
        *,
        log_dir: Path | None = None,
        probe: Callable[[str], bool] = _reachable,
    ) -> None:
        self.name = name
        self._url = cfg.url
        self._probe = probe
        self._start = list(cfg.start)
        self._stop = list(cfg.stop)
        self._env = dict(cfg.start_env)
        self._timeout = cfg.start_timeout
        self._log_dir = log_dir
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_fh: IO[bytes] | None = None
        self._external = False  # reachable before we ever started it: not ours to stop
        self._delegated = False  # started via the stop-command pair, not a child process
        self._starts = 0
        self._failed: str | None = None  # sticky for the run; cleared by shutdown()

    def ensure(self) -> None:
        """Make the engine reachable, starting it if configured to. Raises
        ``EngineStartError`` when it can't be; the failure is sticky until ``shutdown``
        so one dead engine costs one timeout, not one per document."""
        with self._lock:
            if self._failed:
                raise EngineStartError(self._failed)
            if self._external or self._delegated:
                return
            if self._proc is not None:
                if self._proc.poll() is None:
                    return
                # Our child died mid-run (e.g. OOM on a huge scan). Clean up and fall
                # through to a restart, bounded by _MAX_STARTS.
                log.warning("engine died; restarting", engine=self.name, rc=self._proc.returncode)
                self._reap()
            if self._probe(self._url):
                self._external = True
                return
            if not self._start:
                return  # unmanaged engine; the request itself will surface the outage
            if self._starts >= _MAX_STARTS:
                self._fail(f"engine {self.name!r} failed {self._starts} starts this run")
            self._starts += 1
            if self._stop:
                self._start_delegated()
            else:
                self._start_managed()
            self._await_reachable()

    def _fail(self, msg: str) -> NoReturn:
        self._failed = msg
        raise EngineStartError(msg)

    def _merged_env(self) -> dict[str, str]:
        return {**os.environ, **self._env}

    def _start_managed(self) -> None:
        fh = None
        if self._log_dir is not None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            fh = (self._log_dir / f"engine-{self.name}.log").open("ab")
        log.info("starting engine", engine=self.name, argv=self._start)
        try:
            self._proc = subprocess.Popen(
                self._start,
                env=self._merged_env(),
                stdout=fh or subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            if fh is not None:
                fh.close()
            self._fail(f"engine {self.name!r} start command failed: {exc}")
        self._log_fh = fh

    def _start_delegated(self) -> None:
        log.info("starting engine (delegated)", engine=self.name, argv=self._start)
        try:
            proc = subprocess.run(
                self._start,
                env=self._merged_env(),
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._fail(f"engine {self.name!r} start command failed: {exc}")
        if proc.returncode != 0:
            tail = proc.stderr.decode(errors="replace").strip()[-500:]
            self._fail(f"engine {self.name!r} start command exited {proc.returncode}: {tail}")
        self._delegated = True

    def _await_reachable(self) -> None:
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                rc = self._proc.returncode
                self._reap()
                self._fail(f"engine {self.name!r} exited with status {rc} before serving")
            if self._probe(self._url):
                return
            time.sleep(_PROBE_INTERVAL)
        self._stop_started()
        self._fail(f"engine {self.name!r} not reachable within {self._timeout:.0f}s of starting")

    def shutdown(self) -> None:
        """Stop the engine if — and only if — this run started it, and reset all state so a
        later run (or retry) starts from a fresh probe."""
        with self._lock:
            self._stop_started()
            self._external = False
            self._starts = 0
            self._failed = None

    def _stop_started(self) -> None:
        if self._delegated:
            log.info("stopping engine (delegated)", engine=self.name, argv=self._stop)
            try:
                proc = subprocess.run(
                    self._stop,
                    env=self._merged_env(),
                    capture_output=True,
                    timeout=self._timeout,
                    check=False,
                )
                if proc.returncode != 0:
                    tail = proc.stderr.decode(errors="replace").strip()[-500:]
                    log.warning("engine stop command failed", engine=self.name, error=tail)
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.warning("engine stop command failed", engine=self.name, error=str(exc))
            self._delegated = False
        if self._proc is not None and self._proc.poll() is None:
            log.info("stopping engine", engine=self.name)
            self._proc.terminate()
            try:
                self._proc.wait(_STOP_GRACE)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._reap()

    def _reap(self) -> None:
        self._proc = None
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None


class EngineLifecycles:
    """All configured lifecycles for a run. Falsy when no engine has a ``start`` command,
    which keeps every code path a no-op for deployments that never opt in."""

    def __init__(self, entries: dict[str, EngineLifecycle]) -> None:
        self._entries = entries

    def __bool__(self) -> bool:
        return bool(self._entries)

    def get(self, name: str) -> EngineLifecycle | None:
        return self._entries.get(name)

    def shutdown(self) -> None:
        for lc in self._entries.values():
            lc.shutdown()


def build_lifecycles(
    engines: dict[str, EngineConfig], *, log_dir: Path | None = None
) -> EngineLifecycles:
    return EngineLifecycles(
        {
            name: EngineLifecycle(name, cfg, log_dir=log_dir)
            for name, cfg in engines.items()
            if cfg.start
        }
    )


def _raise_system_exit(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


def install_sigterm_handler() -> None:
    """Convert SIGTERM into ``SystemExit`` so ``finally`` teardown runs when launchd (or an
    operator's ``kill``) stops a run mid-flight. Installed only over the default handler and
    only from the main thread; anything else keeps its existing behavior."""
    try:
        if signal.getsignal(signal.SIGTERM) is signal.SIG_DFL:
            signal.signal(signal.SIGTERM, _raise_system_exit)
    except ValueError:  # not the main thread
        pass


class ManagedExtractor:
    """Transparent decorator around an engine adapter: raises the engine before its first
    use, changes nothing else. Identity attributes mirror the inner adapter so caching,
    fingerprints, and the escalate router see the engine itself."""

    def __init__(self, inner: Extractor, lifecycle: EngineLifecycle) -> None:
        self._inner = inner
        self._lifecycle = lifecycle
        self.name = inner.name
        self.version = inner.version
        self.fingerprint = inner.fingerprint

    def extract(self, req: ExtractRequest) -> ExtractionResult:
        self._lifecycle.ensure()
        return self._inner.extract(req)
