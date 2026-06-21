from fundus.config import default_config_path, load_config


def test_load_config(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNDUS_MEILI_KEY", raising=False)
    f = tmp_path / "fundus.toml"
    f.write_text(
        'locales = ["eng", "deu"]\n'
        "[meilisearch]\n"
        'index = "mycorpus"\n'
        "[[sources]]\n"
        'name = "mail"\n'
        'type = "notmuch"\n'
        'query = "tag:inbox"\n'
    )
    cfg = load_config(f)
    assert cfg.meilisearch.index == "mycorpus"
    assert cfg.locales == ["eng", "deu"]
    assert cfg.sources[0].name == "mail"
    assert cfg.sources[0].model_dump()["query"] == "tag:inbox"


def test_load_config_env_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDUS_MEILI_KEY", "secret123")
    f = tmp_path / "fundus.toml"
    f.write_text("")
    assert load_config(f).meilisearch.api_key == "secret123"


def test_load_config_embed_env_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDUS_EMBED_KEY", "embsecret")
    f = tmp_path / "fundus.toml"
    f.write_text("")
    assert load_config(f).embedder.api_key == "embsecret"


def test_load_config_missing_file_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml").meilisearch.index == "corpus"


def test_default_config_path_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_path() == tmp_path / "fundus.toml"
