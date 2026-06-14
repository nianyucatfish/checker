"""LLM config persistence in config.toml."""
import tomllib

from sidecar import config as cfgmod


def test_write_llm_config_replaces_existing_section(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        """[tencent_docs]
client_id = "cid"

[llm]
protocol = "openai"
endpoint = "http://old"
api_key = "old"
model = "old-model"

[user]
reviewer_name = "tester"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cfgmod,
        "get_config",
        lambda: cfgmod.Config(source_path=path),
    )

    written = cfgmod.write_llm_config(
        cfgmod.LLMConfig(
            protocol="anthropic",
            endpoint="https://api.anthropic.com",
            api_key="sk-new",
            model="claude-x",
        )
    )

    assert written == path
    text = path.read_text(encoding="utf-8")
    assert 'client_id = "cid"' in text
    assert 'reviewer_name = "tester"' in text
    assert 'endpoint = "http://old"' not in text
    raw = tomllib.loads(text)
    assert raw["llm"] == {
        "protocol": "anthropic",
        "endpoint": "https://api.anthropic.com",
        "api_key": "sk-new",
        "model": "claude-x",
    }


def test_write_llm_config_appends_missing_section(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[user]\nreviewer_name = "tester"\n', encoding="utf-8")
    monkeypatch.setattr(
        cfgmod,
        "get_config",
        lambda: cfgmod.Config(source_path=path),
    )

    cfgmod.write_llm_config(cfgmod.LLMConfig(endpoint="http://new", api_key="sk"))

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["user"]["reviewer_name"] == "tester"
    assert raw["llm"]["protocol"] == "openai"
    assert raw["llm"]["endpoint"] == "http://new"
    assert raw["llm"]["api_key"] == "sk"
    assert raw["llm"]["model"] == "claude-opus-4-7"


def test_load_config_ignores_legacy_llm_override(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        '[llm]\nendpoint = "http://toml"\napi_key = "toml-key"\n',
        encoding="utf-8",
    )
    (tmp_path / "llm_override.json").write_text(
        '{"endpoint":"http://override","api_key":"override-key"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(cfgmod, "_candidate_paths", lambda: [path])

    cfg = cfgmod.load_config()

    assert cfg.llm.endpoint == "http://toml"
    assert cfg.llm.api_key == "toml-key"


def test_config_path_for_write_uses_env_when_no_config_loaded(tmp_path, monkeypatch):
    path = tmp_path / "custom.toml"
    monkeypatch.setenv("CHECKER_CONFIG", str(path))
    monkeypatch.setattr(cfgmod, "get_config", lambda: cfgmod.Config())

    assert cfgmod.config_path_for_write() == path


def test_from_raw_reads_llm(tmp_path):
    cfg = cfgmod._from_raw(
        {"llm": {"endpoint": "http://new", "api_key": "new"}},
        tmp_path / "config.toml",
    )
    assert cfg.llm.endpoint == "http://new"
    assert cfg.llm.api_key == "new"


def test_from_raw_ignores_legacy_test_llm(tmp_path):
    cfg = cfgmod._from_raw(
        {"test_llm": {"endpoint": "http://old", "api_key": "old"}},
        tmp_path / "config.toml",
    )
    assert cfg.llm.endpoint == ""
    assert cfg.llm.api_key == ""
