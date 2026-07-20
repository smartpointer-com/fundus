import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fundus.cli import app
from fundus.service import manager
from fundus.service.spec import Plan, fundus_command, parse_hhmm

runner = CliRunner()


@pytest.fixture(autouse=True)
def _pretend_darwin(monkeypatch):
    # `fundus service` is gated to macOS, but everything under test here is a pure
    # data transform or fully mocked — lift the gate so the suite runs on Linux CI.
    monkeypatch.setattr(sys, "platform", "darwin")


def _plan(kind="agent", **kw):
    base = dict(
        kind=kind,
        label_prefix="com.test.fundus",
        fundus_bin="/opt/pipx/fundus",
        config_path="/home/u/.config/fundus.toml",
        logs_dir="/home/u/fundus/logs",
        interval_minutes=30,
        full_at="04:00",
        home="/home/u",
        username="u",
        groupname="staff",
    )
    base.update(kw)
    return Plan(**base)


# --- pure helpers ---------------------------------------------------------------------------


def test_parse_hhmm():
    assert parse_hhmm("04:00") == {"Hour": 4, "Minute": 0}
    assert parse_hhmm("23:59") == {"Hour": 23, "Minute": 59}
    assert parse_hhmm("3") == {"Hour": 3, "Minute": 0}
    for bad in ("24:00", "04:60", "-1:00"):
        with pytest.raises(ValueError):
            parse_hhmm(bad)


def test_fundus_command_is_a_direct_exec():
    # No shell wrapper, no secret sourcing — fundus self-sources its env_file at startup.
    assert fundus_command("/opt/pipx/fundus", "/cfg.toml", "index") == \
        ["/opt/pipx/fundus", "index", "--config", "/cfg.toml"]
    assert fundus_command("/b/fundus", "/cfg.toml", "index", "--full") == \
        ["/b/fundus", "index", "--full", "--config", "/cfg.toml"]
    assert fundus_command("/b/fundus", "/cfg.toml", "serve") == \
        ["/b/fundus", "serve", "--config", "/cfg.toml"]


# --- plist generation -----------------------------------------------------------------------


def test_agent_jobs_have_no_owner_and_right_schedule():
    inc, full, srv = _plan("agent").jobs()
    assert inc.label == "com.test.fundus.index"
    assert full.label == "com.test.fundus.index-full"
    p_inc, p_full = inc.to_plist(), full.to_plist()
    assert p_inc["StartInterval"] == 1800 and p_inc["RunAtLoad"] is True
    assert p_full["StartCalendarInterval"] == {"Hour": 4, "Minute": 0}
    assert "RunAtLoad" not in p_full  # never auto-run the heavy reconcile at load
    assert "StartInterval" not in p_full
    for p in (p_inc, p_full):
        assert p["ProcessType"] == "Background" and p["LowPriorityIO"] is True
        assert "UserName" not in p and "GroupName" not in p  # agents run as the session user


def test_serve_job_kept_alive_and_responsive():
    srv = _plan().jobs()[2]
    assert srv.label == "com.test.fundus.serve"
    p = srv.to_plist()
    assert p["KeepAlive"] is True and p["RunAtLoad"] is True  # a server: stay up, start at load
    assert "ProcessType" not in p and "LowPriorityIO" not in p  # responsive, not throttled
    assert "StartInterval" not in p and "StartCalendarInterval" not in p  # no schedule
    assert srv.program_arguments == ["/opt/pipx/fundus", "serve", "--config", "/home/u/.config/fundus.toml"]


def test_include_toggles_select_jobs():
    index_only = [j.label for j in _plan(include_serve=False).jobs()]
    assert index_only == ["com.test.fundus.index", "com.test.fundus.index-full"]
    serve_only = [j.label for j in _plan(include_index=False).jobs()]
    assert serve_only == ["com.test.fundus.serve"]


def test_docling_absent_by_default():
    assert "com.test.fundus.docling" not in [j.label for j in _plan().jobs()]


def test_docling_job_kept_alive_with_command_and_merged_env():
    plan = _plan(
        include_docling=True,
        docling_command=["/venv/bin/docling-serve", "run", "--port", "5001"],
        docling_environment={"PATH": "/venv/bin:/usr/bin"},
    )
    docling = {j.label: j for j in plan.jobs()}["com.test.fundus.docling"]
    # execs the user's command directly, not the fundus binary
    assert docling.program_arguments == ["/venv/bin/docling-serve", "run", "--port", "5001"]
    p = docling.to_plist()
    assert p["KeepAlive"] is True and p["RunAtLoad"] is True  # a server: stay up
    assert "ProcessType" not in p  # responsive, not throttled like the index jobs
    assert p["EnvironmentVariables"]["PATH"] == "/venv/bin:/usr/bin"  # override wins
    assert p["EnvironmentVariables"]["HOME"] == "/home/u"  # base env preserved
    assert p["StandardOutPath"] == "/home/u/fundus/logs/docling.log"


def test_daemon_jobs_run_as_user_not_root():
    inc = _plan("daemon").jobs()[0].to_plist()
    assert inc["UserName"] == "u" and inc["GroupName"] == "staff"
    assert inc["EnvironmentVariables"]["HOME"] == "/home/u"  # daemons get a bare env; HOME is set


def test_job_logs_go_to_the_logs_dir():
    inc, full, srv = _plan().jobs()
    assert inc.to_plist()["StandardOutPath"] == "/home/u/fundus/logs/index.log"
    assert full.to_plist()["StandardErrorPath"] == "/home/u/fundus/logs/index-full.log"
    assert srv.to_plist()["StandardOutPath"] == "/home/u/fundus/logs/serve.log"


# --- launchctl targeting --------------------------------------------------------------------


def test_domain_targets():
    assert manager.domain_target("daemon", 501) == "system"
    assert manager.domain_target("agent", 501) == "gui/501"


def test_plist_paths():
    home = Path("/home/u")
    assert manager.plist_path("daemon", "x.index", home) == Path("/Library/LaunchDaemons/x.index.plist")
    assert manager.plist_path("agent", "x.index", home) == home / "Library/LaunchAgents/x.index.plist"


def test_all_labels_covers_index_serve_and_docling():
    assert manager.all_labels("com.x") == [
        "com.x.index", "com.x.index-full", "com.x.serve", "com.x.docling",
    ]


def test_job_label_routes_to_docling():
    from fundus.service.cli import _job_label

    assert _job_label("com.x", full=False, serve=False, docling=True) == "com.x.docling"
    assert _job_label("com.x", full=False, serve=True, docling=True) == "com.x.serve"  # serve wins
    assert _job_label("com.x", full=True, serve=False, docling=False) == "com.x.index-full"
    assert _job_label("com.x", full=False, serve=False, docling=False) == "com.x.index"


def test_ensure_docling_command_resolves_bare_name_to_absolute():
    import shutil

    resolved = manager.ensure_docling_command(["sh", "-c", "true"])
    assert resolved == [shutil.which("sh"), "-c", "true"]  # launchd needs an absolute program path


def test_ensure_docling_command_keeps_absolute_and_rejects_empty(tmp_path):
    exe = tmp_path / "docling-serve"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert manager.ensure_docling_command([str(exe), "run"]) == [str(exe), "run"]
    with pytest.raises(manager.ServiceError):
        manager.ensure_docling_command([])


# --- binary validation ----------------------------------------------------------------------


def test_ensure_installed_binary_refuses_editable(monkeypatch):
    monkeypatch.setattr(manager, "is_editable_install", lambda: True)
    with pytest.raises(manager.ServiceError, match="editable dev build"):
        manager.ensure_installed_binary()


def test_ensure_installed_binary_accepts_real_install(monkeypatch, tmp_path):
    exe = tmp_path / "fundus"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(manager, "is_editable_install", lambda: False)
    monkeypatch.setattr(manager, "self_binary", lambda: exe)
    assert manager.ensure_installed_binary() == exe


# --- CLI ------------------------------------------------------------------------------------


def test_service_help_lists_subcommands():
    result = runner.invoke(app, ["service", "--help"])
    assert result.exit_code == 0
    for sub in ("install", "uninstall", "status", "restart", "run"):
        assert sub in result.output


def test_service_install_refuses_dev_build(monkeypatch, tmp_path):
    # Guard the user's real environment: install must stop on an editable build before any launchctl.
    monkeypatch.setattr(manager, "is_editable_install", lambda: True)
    cfg = tmp_path / "fundus.toml"
    cfg.write_text("")
    result = runner.invoke(app, ["service", "install", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "make install" in result.output


def test_service_install_rejects_no_index_no_serve(tmp_path):
    cfg = tmp_path / "fundus.toml"
    cfg.write_text("")
    result = runner.invoke(app, ["service", "install", "--no-index", "--no-serve", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "nothing to install" in result.output


def test_service_install_warns_without_env_file(monkeypatch, tmp_path):
    # The real foot-gun: launchd jobs without an env_file can't see shell secrets.
    monkeypatch.setattr(manager, "ensure_installed_binary", lambda: Path("/opt/pipx/fundus"))
    monkeypatch.setattr(manager, "install", lambda plan: [j.label for j in plan.jobs()])
    cfg = tmp_path / "fundus.toml"
    cfg.write_text("")  # no [service].env_file
    result = runner.invoke(app, ["service", "install", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "no env_file" in result.output


def test_service_install_skips_docling_enabled_without_command(monkeypatch, tmp_path):
    # A config that enables docling but omits the command must warn and skip — NOT block index/serve.
    monkeypatch.setattr(manager, "ensure_installed_binary", lambda: Path("/opt/pipx/fundus"))
    monkeypatch.setattr(manager, "install", lambda plan: [j.label for j in plan.jobs()])
    cfg = tmp_path / "fundus.toml"
    cfg.write_text("[service.docling]\nenabled = true\n")  # enabled, no command
    result = runner.invoke(app, ["service", "install", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "skipping the docling job" in result.output
    assert "fundus.docling" not in result.output
    assert "fundus.serve" in result.output  # unrelated jobs still installed


def test_service_install_explicit_docling_requires_command(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "ensure_installed_binary", lambda: Path("/opt/pipx/fundus"))
    cfg = tmp_path / "fundus.toml"
    cfg.write_text("")
    result = runner.invoke(app, ["service", "install", "--docling", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "command is empty" in result.output


def test_service_install_rejects_unexecutable_docling_command(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "ensure_installed_binary", lambda: Path("/opt/pipx/fundus"))
    cfg = tmp_path / "fundus.toml"
    cfg.write_text('[service.docling]\nenabled = true\ncommand = ["/no/such/docling-serve", "run"]\n')
    result = runner.invoke(app, ["service", "install", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "not executable" in result.output


def test_service_install_includes_docling_with_valid_command(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "ensure_installed_binary", lambda: Path("/opt/pipx/fundus"))
    monkeypatch.setattr(manager, "install", lambda plan: [j.label for j in plan.jobs()])
    cfg = tmp_path / "fundus.toml"
    cfg.write_text(
        f'[service.docling]\nenabled = true\ncommand = ["{sys.executable}", "-m", "http.server"]\n'
    )
    result = runner.invoke(app, ["service", "install", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "fundus.docling" in result.output
    assert "docling:" in result.output  # the summary line for the installed job
