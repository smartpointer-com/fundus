"""On-demand engine lifecycle: lazy start, ownership, delegated commands, failure modes.

Reachability is faked through the ``probe`` hook (marker files stand in for a listening
server), so the tests exercise real process management without binding sockets.
"""

import sys
import time

import pytest
from pydantic import ValidationError

from fundus.config import EngineConfig, ExtractorConfig, FundusConfig
from fundus.extract.base import ExtractRequest
from fundus.extract.lifecycle import (
    EngineLifecycle,
    EngineStartError,
    ManagedExtractor,
    build_lifecycles,
)
from fundus.extract.registry import build_extractor
from fundus.extract.tika import TikaExtractor
from fundus.models import Block, DocMeta, EngineRef, ExtractionResult

URL = "http://127.0.0.1:1"  # never dialed; every test injects a probe

# A "server": touches its marker file, then serves (sleeps) until terminated.
SERVER = "import pathlib, time; pathlib.Path({marker!r}).touch(); time.sleep(600)"


def cfg(**kw):
    return EngineConfig(url=URL, **kw)


def test_managed_child_starts_lazily_and_stops(tmp_path):
    marker = tmp_path / "up"
    lc = EngineLifecycle(
        "eng",
        cfg(start=[sys.executable, "-c", SERVER.format(marker=str(marker))], start_timeout=20),
        log_dir=tmp_path,
        probe=lambda url: marker.exists(),
    )
    assert not marker.exists()  # construction starts nothing
    lc.ensure()
    assert marker.exists()
    proc = lc._proc
    assert proc is not None and proc.poll() is None
    lc.ensure()  # idempotent; must not spawn a second child
    assert lc._proc is proc
    lc.shutdown()
    assert proc.poll() is not None  # terminated
    assert (tmp_path / "engine-eng.log").exists()


def test_already_reachable_engine_is_reused_and_never_stopped(tmp_path):
    marker = tmp_path / "started-by-lifecycle"
    lc = EngineLifecycle(
        "eng",
        cfg(start=[sys.executable, "-c", f"open({str(marker)!r}, 'w')"]),
        log_dir=tmp_path,
        probe=lambda url: True,  # something else already serves this URL
    )
    lc.ensure()
    assert not marker.exists()  # reachable already -> start command never ran
    lc.shutdown()  # not ours -> nothing to stop (and nothing crashes)


def test_delegated_start_and_stop(tmp_path):
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    lc = EngineLifecycle(
        "eng",
        cfg(
            start=[sys.executable, "-c", f"open({str(started)!r}, 'w')"],
            stop=[sys.executable, "-c", f"open({str(stopped)!r}, 'w')"],
            start_timeout=20,
        ),
        log_dir=tmp_path,
        probe=lambda url: started.exists(),
    )
    lc.ensure()
    assert started.exists() and not stopped.exists()
    lc.shutdown()
    assert stopped.exists()


def test_start_env_reaches_the_child(tmp_path):
    marker = tmp_path / "env"
    script = f"import os, pathlib; pathlib.Path({str(marker)!r}).write_text(os.environ['FUNDUS_TEST_ENGINE_ENV']); import time; time.sleep(600)"
    lc = EngineLifecycle(
        "eng",
        cfg(start=[sys.executable, "-c", script], start_env={"FUNDUS_TEST_ENGINE_ENV": "v1"}),
        log_dir=tmp_path,
        probe=lambda url: marker.exists(),
    )
    lc.ensure()
    assert marker.read_text() == "v1"
    lc.shutdown()


def test_start_failure_is_sticky_until_shutdown(tmp_path):
    lc = EngineLifecycle(
        "eng",
        cfg(start=[sys.executable, "-c", "raise SystemExit(3)"], start_timeout=10),
        log_dir=tmp_path,
        probe=lambda url: False,
    )
    with pytest.raises(EngineStartError, match="exited with status 3"):
        lc.ensure()
    began = time.monotonic()
    with pytest.raises(EngineStartError):
        lc.ensure()  # sticky: fails fast, no second start attempt / timeout wait
    assert time.monotonic() - began < 1.0
    lc.shutdown()  # resets the failure for the next run


def test_unreachable_after_timeout_stops_the_child(tmp_path):
    lc = EngineLifecycle(
        "eng",
        cfg(start=[sys.executable, "-c", "import time; time.sleep(600)"], start_timeout=1),
        log_dir=tmp_path,
        probe=lambda url: False,  # child runs but never answers
    )
    with pytest.raises(EngineStartError, match="not reachable within"):
        lc.ensure()
    assert lc._proc is None  # the useless child was stopped and reaped


def test_stop_without_start_is_rejected():
    with pytest.raises(ValidationError):
        EngineConfig(url=URL, stop=["docker", "stop", "x"])


def test_build_lifecycles_covers_only_engines_with_start():
    engines = {
        "managed": EngineConfig(url=URL, start=["x"]),
        "always-on": EngineConfig(url=URL),
    }
    lcs = build_lifecycles(engines)
    assert lcs and lcs.get("managed") is not None and lcs.get("always-on") is None
    assert not build_lifecycles({"always-on": engines["always-on"]})


def test_registry_wraps_exactly_the_managed_engines():
    config = ExtractorConfig(engines={"tika": EngineConfig(url=URL, start=["x"])})
    lcs = build_lifecycles(config.engines)
    managed = build_extractor("tika", config, lcs)
    assert isinstance(managed, ManagedExtractor)
    assert (managed.name, managed.version, managed.fingerprint) == ("tika", "unknown", "")
    assert isinstance(build_extractor("tika", config), TikaExtractor)  # no lifecycles -> bare


class RecordingLifecycle:
    def __init__(self):
        self.ensured = 0

    def ensure(self):
        self.ensured += 1


class FakeExtractor:
    name = "fake"
    version = "1"
    fingerprint = ""

    def extract(self, req):
        return ExtractionResult(
            engine=EngineRef(name="fake", version="1"),
            blocks=[Block(type="paragraph", text="x")],
            markdown="x",
            metadata=DocMeta(),
        )


def test_managed_extractor_raises_engine_on_first_use_only():
    lc = RecordingLifecycle()
    ex = ManagedExtractor(FakeExtractor(), lc)
    assert lc.ensured == 0  # construction (build_pipeline, fundus init, ...) starts nothing
    ex.extract(ExtractRequest(data=b"x", mime_type="application/pdf"))
    assert lc.ensured == 1


class RecordingLifecycles:
    def __init__(self):
        self.shutdowns = 0

    def __bool__(self):
        return True

    def get(self, name):
        return None

    def shutdown(self):
        self.shutdowns += 1


class NullSink:
    def ensure_schema(self, settings):
        pass

    def upsert(self, docs):
        pass

    def delete_missing(self, source, live):
        return 0

    def delete_parents(self, parent_ids):
        return 0

    def indexed_manifest(self, source):
        return {}


class DictCache:
    def __init__(self):
        self.d = {}

    def get(self, key):
        return self.d.get(key.as_str())

    def put(self, key, res):
        self.d[key.as_str()] = res


class DictState:
    def __init__(self):
        self.d = {}

    def get_cursor(self, s):
        return self.d.get(s)

    def set_cursor(self, s, c):
        self.d[s] = c


def _pipeline(lifecycles):
    from fundus.core.pipeline import Pipeline

    return Pipeline(
        FundusConfig(),
        NullSink(),
        FakeExtractor(),
        DictCache(),
        DictState(),
        lifecycles=lifecycles,
    )


def test_pipeline_index_stops_started_engines():
    lcs = RecordingLifecycles()
    _pipeline(lcs).index()
    assert lcs.shutdowns == 1


def test_pipeline_reparse_stops_started_engines():
    lcs = RecordingLifecycles()

    class EmptyTree:
        name = "docs"

        def load(self, nid):
            return None

    _pipeline(lcs).reparse(EmptyTree(), [])
    assert lcs.shutdowns == 1
