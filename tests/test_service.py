from pathlib import Path

import pytest
from typer.testing import CliRunner

from fundus.cli import app
from fundus.service import manager
from fundus.service.spec import Plan, fundus_command, parse_hhmm

runner = CliRunner()


def _plan(kind="agent", **kw):
    base = dict(
        kind=kind,
        label_prefix="com.test.fundus",
        fundus_bin="/opt/pipx/fundus",
        config_path="/home/u/.config/fundus.toml",
        env_file="/home/u/.secrets/fundus.env",
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


def test_fundus_command_sources_secrets_and_execs():
    cmd = fundus_command("/opt/pipx/fundus", "/cfg.toml", "/sec.env", "index")
    assert cmd[:2] == ["/bin/bash", "-c"]
    script = cmd[2]
    assert ". /sec.env" in script and "set -a" in script  # secrets sourced at runtime
    assert "exec /opt/pipx/fundus index --config /cfg.toml" in script


def test_fundus_command_full_and_no_env_file():
    script = fundus_command("/b/fundus", "/cfg.toml", None, "index", "--full")[2]
    assert "index --full --config /cfg.toml" in script
    assert "set -a" not in script  # nothing to source


def test_fundus_command_serve():
    script = fundus_command("/b/fundus", "/cfg.toml", None, "serve")[2]
    assert "exec /b/fundus serve --config /cfg.toml" in script


def test_fundus_command_quotes_paths_with_spaces():
    script = fundus_command("/b/fundus", "/with space/cfg.toml", None, "index")[2]
    assert "'/with space/cfg.toml'" in script


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
    assert "serve --config" in srv.program_arguments[2]


def test_include_toggles_select_jobs():
    index_only = [j.label for j in _plan(include_serve=False).jobs()]
    assert index_only == ["com.test.fundus.index", "com.test.fundus.index-full"]
    serve_only = [j.label for j in _plan(include_index=False).jobs()]
    assert serve_only == ["com.test.fundus.serve"]


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


def test_all_labels_covers_index_and_serve():
    assert manager.all_labels("com.x") == ["com.x.index", "com.x.index-full", "com.x.serve"]


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
