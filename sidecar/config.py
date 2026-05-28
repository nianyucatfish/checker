"""
sidecar.config — 加载用户配置(外部服务凭证 + 应用偏好)。

查找顺序:
  1. 环境变量 CHECKER_CONFIG 指定的路径
  2. 仓库根目录 config.toml(开发期)
  3. app_config_dir/config.toml(打包后)

文件不存在或字段为空都不抛错;具体工具调用时再校验自己需要的字段。
sidecar 启动本身不依赖配置(凭证仅在调到对应外部 API 时才需要)。
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


APP_DIR_NAME = "AudioQC"


def _app_config_dir() -> Path:
    """跨平台拿用户配置目录。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / APP_DIR_NAME


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("CHECKER_CONFIG")
    if env:
        paths.append(Path(env))
    repo_root = Path(__file__).resolve().parent.parent
    paths.append(repo_root / "config.toml")
    paths.append(_app_config_dir() / "config.toml")
    return paths


@dataclass
class AnthropicConfig:
    api_key: str = ""
    model: str = "claude-sonnet-4-6"
    base_url: str = ""


@dataclass
class TencentDocsConfig:
    client_id: str = ""
    access_token: str = ""
    open_id: str = ""
    access_token_expires_at: str = ""
    spreadsheet_id: str = ""
    sheet_id: str = ""


@dataclass
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""


@dataclass
class TestLLMConfig:
    """测试期 LLM endpoint(OpenAI 兼容代理)。

    与 [anthropic] 分开,production 切回 Anthropic SDK 时 [anthropic] 字段直接生效,
    本节可清空。当前只用于 sidebar 聊天测试。
    """
    endpoint: str = ""
    api_key: str = ""
    model: str = "claude-opus-4-7"


@dataclass
class AgentSandboxConfig:
    sheet_fixture_path: str = ""


@dataclass
class PreferencesConfig:
    execution_mode: str = "confirm"


@dataclass
class UserConfig:
    """当前用户身份。仅 sidecar 进程内使用,绝不进 agent prompt / tool args。

    LLM 调到的工具签名上不暴露这个字段;sidecar 内部读取来过滤"我的"数据。
    多人多机共享软件时各自 config.toml 各填各的。
    """
    reviewer_name: str = ""


@dataclass
class Config:
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    tencent_docs: TencentDocsConfig = field(default_factory=TencentDocsConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    test_llm: TestLLMConfig = field(default_factory=TestLLMConfig)
    agent_sandbox: AgentSandboxConfig = field(default_factory=AgentSandboxConfig)
    preferences: PreferencesConfig = field(default_factory=PreferencesConfig)
    user: UserConfig = field(default_factory=UserConfig)
    source_path: Path | None = None  # 实际加载自哪个文件;None 表示全 default


def _from_raw(raw: dict, source: Path) -> Config:
    a = raw.get("anthropic", {})
    t = raw.get("tencent_docs", {})
    f = raw.get("feishu", {})
    l = raw.get("test_llm", {})
    s = raw.get("agent_sandbox", {})
    p = raw.get("preferences", {})
    u = raw.get("user", {})
    return Config(
        anthropic=AnthropicConfig(
            api_key=str(a.get("api_key", "")),
            model=str(a.get("model", "claude-sonnet-4-6")),
            base_url=str(a.get("base_url", "")),
        ),
        tencent_docs=TencentDocsConfig(
            client_id=str(t.get("client_id", "")),
            access_token=str(t.get("access_token", "")),
            open_id=str(t.get("open_id", "")),
            access_token_expires_at=str(t.get("access_token_expires_at", "")),
            spreadsheet_id=str(t.get("spreadsheet_id", "")),
            sheet_id=str(t.get("sheet_id", "")),
        ),
        feishu=FeishuConfig(
            app_id=str(f.get("app_id", "")),
            app_secret=str(f.get("app_secret", "")),
        ),
        test_llm=TestLLMConfig(
            endpoint=str(l.get("endpoint", "")),
            api_key=str(l.get("api_key", "")),
            model=str(l.get("model", "claude-opus-4-7")),
        ),
        agent_sandbox=AgentSandboxConfig(
            sheet_fixture_path=str(s.get("sheet_fixture_path", "")),
        ),
        preferences=PreferencesConfig(
            execution_mode=str(p.get("execution_mode", "confirm")),
        ),
        user=UserConfig(
            reviewer_name=str(u.get("reviewer_name", "")),
        ),
        source_path=source,
    )


def load_config() -> Config:
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as e:
            raise RuntimeError(f"config file invalid: {path}\n{e}") from e
        return _from_raw(raw, path)
    return Config()


_cached: Config | None = None


def get_config() -> Config:
    global _cached
    if _cached is None:
        _cached = load_config()
    return _cached


def reload_config() -> Config:
    """开发期手动 reload(改了 config.toml 不必重启 sidecar)。"""
    global _cached
    _cached = None
    return get_config()
