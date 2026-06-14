"""llm_override.json 覆盖 [test_llm] 的合并逻辑。"""
from sidecar import config as cfgmod


def test_apply_llm_override_merges_and_keeps_key(tmp_path):
    cfg = cfgmod.Config()
    cfg.source_path = tmp_path / "config.toml"
    cfg.test_llm.endpoint = "http://base"
    cfg.test_llm.api_key = "K1"
    cfg.test_llm.model = "m1"
    cfg.test_llm.protocol = "openai"
    # override 不给 api_key → 应保留原 key
    (tmp_path / "llm_override.json").write_text(
        '{"protocol":"anthropic","endpoint":"https://api.anthropic.com","model":"claude-x"}',
        encoding="utf-8",
    )

    out = cfgmod._apply_llm_override(cfg)
    assert out.test_llm.protocol == "anthropic"
    assert out.test_llm.endpoint == "https://api.anthropic.com"
    assert out.test_llm.model == "claude-x"
    assert out.test_llm.api_key == "K1"  # 没给 → 保留 toml 的


def test_apply_llm_override_sets_key(tmp_path):
    cfg = cfgmod.Config()
    cfg.source_path = tmp_path / "config.toml"
    (tmp_path / "llm_override.json").write_text('{"api_key":"sk-new"}', encoding="utf-8")
    out = cfgmod._apply_llm_override(cfg)
    assert out.test_llm.api_key == "sk-new"


def test_apply_llm_override_noop_without_file(tmp_path):
    cfg = cfgmod.Config()
    cfg.source_path = tmp_path / "config.toml"
    cfg.test_llm.endpoint = "http://base"
    out = cfgmod._apply_llm_override(cfg)
    assert out.test_llm.endpoint == "http://base"


def test_override_path_next_to_config(tmp_path):
    cfg = cfgmod.Config()
    cfg.source_path = tmp_path / "config.toml"
    assert cfgmod._llm_override_path(cfg.source_path) == tmp_path / "llm_override.json"
