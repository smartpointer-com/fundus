from pathlib import Path

from fundus.config import FundusConfig, default_config_path, load_config


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


def test_data_root_defaults_to_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cfg = FundusConfig()
    assert cfg.data_root() == tmp_path / "fundus"
    # everything is consolidated under the one root, in named subdirs
    assert cfg.meili_dir() == tmp_path / "fundus" / "meili"
    assert cfg.extraction_cache_path() == tmp_path / "fundus" / "cache" / "extractions.db"
    assert cfg.embed_cache_path() == tmp_path / "fundus" / "cache" / "embeddings.db"
    assert cfg.cursors_path() == tmp_path / "fundus" / "state" / "cursors.json"
    assert cfg.lock_path() == tmp_path / "fundus" / "state" / "fundus.lock"


def test_data_dir_overrides_root_and_expands_user(tmp_path):
    cfg = FundusConfig.model_validate({"storage": {"data_dir": "~/myfundus"}})
    assert cfg.data_root() == Path.home() / "myfundus"
    assert cfg.meili_dir() == Path.home() / "myfundus" / "meili"


def test_data_dir_from_loaded_config(tmp_path):
    f = tmp_path / "fundus.toml"
    f.write_text(f'[storage]\ndata_dir = "{tmp_path / "store"}"\n')
    cfg = load_config(f)
    assert cfg.data_root() == tmp_path / "store"


def test_load_config_sources_env_file(tmp_path, monkeypatch):
    # monkeypatch.delenv records the originals so the sourced vars are restored on teardown.
    monkeypatch.delenv("FUNDUS_MEILI_KEY", raising=False)
    monkeypatch.delenv("FUNDUS_SERVE_TOKEN", raising=False)
    env = tmp_path / "secrets.env"
    env.write_text('FUNDUS_MEILI_KEY="from-file"\nexport FUNDUS_SERVE_TOKEN=tok123\n')
    cfg_file = tmp_path / "fundus.toml"
    cfg_file.write_text(f'env_file = "{env}"\n')
    cfg = load_config(cfg_file)
    assert cfg.meilisearch.api_key == "from-file"  # quoted value, sourced via bash then picked up
    assert cfg.serve.token == "tok123"  # `export KEY=val` form also handled


def test_load_config_ignores_missing_env_file(tmp_path):
    f = tmp_path / "fundus.toml"
    f.write_text(f'env_file = "{tmp_path / "nope.env"}"\n')
    assert load_config(f).meilisearch.index == "corpus"  # absent file is skipped, not fatal
