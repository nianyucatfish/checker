"""Runtime path selection and Electron environment overrides."""

from pathlib import Path

from sidecar import paths


def test_development_defaults_keep_repo_layout(monkeypatch):
    for name in (
        "CHECKER_DATA_DIR",
        "CHECKER_CACHE_DIR",
        "CHECKER_LOG_DIR",
        "CHECKER_CONFIG",
        "CHECKER_RESOURCE_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)

    root = paths.repo_root()
    assert paths.data_dir() == root / "cache"
    assert paths.cache_dir() == root / "cache"
    assert paths.review_log_path() == root / "cache" / "review_log.jsonl"
    assert paths.agent_upstream_log_path() == root / "tmp" / "agent_upstream.jsonl"
    assert paths.midi_debug_dir() == root / "tmp"
    assert paths.resource_root() == root
    assert paths.config_candidates()[0] == root / "config.toml"


def test_electron_overrides_all_runtime_roots(tmp_path, monkeypatch):
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    logs = tmp_path / "logs"
    resources = tmp_path / "resources"
    config = tmp_path / "settings" / "config.toml"
    monkeypatch.setenv("CHECKER_DATA_DIR", str(data))
    monkeypatch.setenv("CHECKER_CACHE_DIR", str(cache))
    monkeypatch.setenv("CHECKER_LOG_DIR", str(logs))
    monkeypatch.setenv("CHECKER_RESOURCE_ROOT", str(resources))
    monkeypatch.setenv("CHECKER_CONFIG", str(config))

    assert paths.state_tree_dir() == data / "state_tree"
    assert paths.sheet_cache_path() == cache / "sheet_cache.json"
    assert paths.review_log_path() == logs / "review_log.jsonl"
    assert paths.agent_upstream_log_path() == logs / "agent_upstream.jsonl"
    assert paths.midi_debug_dir() == logs
    assert paths.resource_root() == resources
    assert paths.config_candidates()[0] == config
    assert paths.default_config_write_path() == config


def test_platform_config_fallback_windows(monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "C:/Users/test/AppData/Roaming")
    assert paths.app_config_dir() == Path("C:/Users/test/AppData/Roaming") / "AudioQC"


def test_platform_config_fallback_linux(monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/test/xdg-config")
    assert paths.app_config_dir() == Path("/home/test/xdg-config/AudioQC")


def test_path_lookup_does_not_create_directories(tmp_path, monkeypatch):
    data = tmp_path / "not-created" / "data"
    cache = tmp_path / "not-created" / "cache"
    logs = tmp_path / "not-created" / "logs"
    monkeypatch.setenv("CHECKER_DATA_DIR", str(data))
    monkeypatch.setenv("CHECKER_CACHE_DIR", str(cache))
    monkeypatch.setenv("CHECKER_LOG_DIR", str(logs))

    paths.state_tree_dir()
    paths.sheet_cache_path()
    paths.review_log_path()
    assert not data.exists()
    assert not cache.exists()
    assert not logs.exists()
