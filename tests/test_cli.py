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
    for command in ("index", "query", "serve", "sources", "bakeoff", "init"):
        assert command in result.output
