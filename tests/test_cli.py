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
    assert "mail\tnotmuch" in result.output
    assert "docs\tfiles" in result.output


def test_cli_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("index", "query", "serve", "sources", "bakeoff", "init"):
        assert command in result.output
