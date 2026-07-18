from typer.testing import CliRunner

from fundus.cli import app

runner = CliRunner()


def test_cli_sources(tmp_path):
    f = tmp_path / "fundus.toml"
    f.write_text(
        '[[sources]]\nname = "mail"\ntype = "notmuch"\n'
        '[[sources]]\nname = "docs"\ntype = "files"\nroots = ["/tmp"]\n'
    )
    result = runner.invoke(app, ["sources", "--config", str(f)])
    assert result.exit_code == 0
    assert "mail" in result.output and "notmuch" in result.output
    assert "docs" in result.output and "files" in result.output
    assert "roots" in result.output  # connector detail is shown, not just name/type


def test_cli_sources_json(tmp_path):
    import json

    f = tmp_path / "fundus.toml"
    f.write_text(
        '[[sources]]\nname = "mail"\ntype = "notmuch"\n'
        '[[sources]]\nname = "docs"\ntype = "files"\nroots = ["/tmp"]\n'
    )
    result = runner.invoke(app, ["sources", "--json", "--config", str(f)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {r["name"] for r in data} == {"mail", "docs"}
    assert any(r["type"] == "files" and r["config"].get("roots") == ["/tmp"] for r in data)


def test_cli_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("index", "query", "serve", "sources", "bakeoff", "init", "paths", "connect"):
        assert command in result.output


def _connect_config(tmp_path, extra=""):
    f = tmp_path / "fundus.toml"
    f.write_text(f'[serve]\nport = 9999\n{extra}')
    return f


def test_cli_connect_prints_filled_in_registrations(tmp_path, monkeypatch):
    import json

    monkeypatch.delenv("FUNDUS_SERVE_TOKEN", raising=False)
    f = _connect_config(tmp_path, 'token = "sekrit"\n')
    result = runner.invoke(app, ["connect", "--config", str(f)])
    assert result.exit_code == 0
    assert "openclaw mcp add fundus --url http://127.0.0.1:9999/mcp" in result.output
    assert "claude mcp add --transport http fundus http://127.0.0.1:9999/mcp" in result.output
    assert result.output.count("Bearer sekrit") == 3  # all three blocks carry the real token
    stanza = result.output[result.output.index("{"):]
    assert json.loads(stanza)["fundus"]["url"] == "http://127.0.0.1:9999/mcp"


def test_cli_connect_single_client_is_bare(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNDUS_SERVE_TOKEN", raising=False)
    f = _connect_config(tmp_path, 'token = "sekrit"\n')
    result = runner.invoke(app, ["connect", "openclaw", "--config", str(f)])
    assert result.exit_code == 0
    assert result.output.strip().startswith("openclaw mcp add")  # no section headers: pipeable


def test_cli_connect_warns_when_token_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNDUS_SERVE_TOKEN", raising=False)
    result = runner.invoke(app, ["connect", "--config", str(_connect_config(tmp_path))])
    assert result.exit_code == 0
    assert "$FUNDUS_SERVE_TOKEN" in result.output  # placeholder keeps the lines paste-able
    assert "no serve token configured" in result.output


def test_cli_connect_sse_transport(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNDUS_SERVE_TOKEN", raising=False)
    f = _connect_config(tmp_path, 'token = "sekrit"\ntransport = "sse"\n')
    result = runner.invoke(app, ["connect", "claude", "--config", str(f)])
    assert "--transport sse" in result.output
    assert "http://127.0.0.1:9999/sse" in result.output


def test_cli_connect_stdio_prints_spawn_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNDUS_SERVE_TOKEN", raising=False)
    f = _connect_config(tmp_path, 'transport = "stdio"\n')
    result = runner.invoke(app, ["connect", "--config", str(f)])
    assert result.exit_code == 0
    assert "spawns the server itself" in result.output


def test_cli_connect_rejects_unknown_client(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNDUS_SERVE_TOKEN", raising=False)
    result = runner.invoke(app, ["connect", "cursor", "--config", str(_connect_config(tmp_path))])
    assert result.exit_code == 2
    assert "unknown client" in result.output


def test_cli_paths_reports_consolidated_root(tmp_path):
    f = tmp_path / "fundus.toml"
    f.write_text(f'[storage]\ndata_dir = "{tmp_path / "store"}"\n')
    result = runner.invoke(app, ["paths", "--config", str(f)])
    assert result.exit_code == 0
    root = str(tmp_path / "store")
    assert root in result.output
    assert f"{root}/meili" in result.output  # everything sits under the one root
    assert f"{root}/cache" in result.output and f"{root}/state" in result.output


def test_cli_paths_meili_data_is_machine_readable(tmp_path):
    f = tmp_path / "fundus.toml"
    f.write_text(f'[storage]\ndata_dir = "{tmp_path / "store"}"\n')
    result = runner.invoke(app, ["paths", "--meili-data", "--config", str(f)])
    assert result.exit_code == 0
    assert result.output.strip() == str(tmp_path / "store" / "meili")  # bare path for `make up`
