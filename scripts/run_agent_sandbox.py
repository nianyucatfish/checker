"""Run a minimal tool-calling agent against the local sandbox.

This is a development runner, not the Electron production agent loop. It calls the
configured OpenAI-compatible test LLM and exposes the sidecar MCP tool functions
in-process, with an extra sandbox path guard.

Usage:
    python scripts/create_agent_sandbox.py
    python scripts/run_agent_sandbox.py --song-folder "飞儿乐队_你的微笑_吴行健" --song-name "你的微笑"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

import httpx


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SANDBOX = ROOT / "tmp" / "agent_sandbox"
DEFAULT_CONFIG = DEFAULT_SANDBOX / "config.agent-test.toml"
DEFAULT_WORKSPACE = DEFAULT_SANDBOX / "workspace"


ToolFn = Callable[..., dict[str, Any]]


def _ensure_config_env(config_path: Path) -> None:
    os.environ.setdefault("CHECKER_CONFIG", str(config_path.resolve()))


def _is_within(path: str | Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _jsonable(obj: Any) -> Any:
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except TypeError:
        return str(obj)


def _compact_tool_result(result: dict[str, Any], max_chars: int = 12000) -> str:
    text = json.dumps(_jsonable(result), ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"


def _normalize_op(op: dict[str, Any], workspace: Path) -> dict[str, Any]:
    out = dict(op)
    if "op" in out and "type" not in out:
        out["type"] = out.pop("op")
    for key in ("src", "dst", "path", "dst_dir"):
        value = out.get(key)
        if isinstance(value, str) and value and not os.path.isabs(value):
            out[key] = str((workspace / value).resolve())
    return out


def _normalize_ops(ops: list[dict[str, Any]], workspace: Path) -> list[dict[str, Any]]:
    return [_normalize_op(op, workspace) for op in ops]


def _schema_tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _build_tools(workspace_root: Path) -> tuple[list[dict[str, Any]], dict[str, ToolFn]]:
    from sidecar import mcp_server

    workspace = workspace_root.resolve()

    def guard_path(path: str, label: str = "path") -> None:
        if not _is_within(path, workspace):
            raise ValueError(f"{label} outside sandbox workspace: {path}")

    def state_tree_read(song: str) -> dict[str, Any]:
        return mcp_server.state_tree_read(song=song)

    def state_tree_update(song: str, state_id: str, done: bool, note: str | None = None) -> dict[str, Any]:
        return mcp_server.state_tree_update(song=song, state_id=state_id, done=done, note=note)

    def sheet_get_song_meta(song_name: str) -> dict[str, Any]:
        return mcp_server.sheet_get_song_meta(song_name=song_name)

    def sheet_list_my_pending() -> dict[str, Any]:
        return mcp_server.sheet_list_my_pending()

    def audit_list_errors(song_path: str) -> dict[str, Any]:
        guard_path(song_path, "song_path")
        return mcp_server.audit_list_errors(song_path=song_path)

    def fs_list_dir(path: str, max_depth: int = 2) -> dict[str, Any]:
        guard_path(path)
        return mcp_server.fs_list_dir(path=path, max_depth=max_depth)

    def read_text_file(path: str, line_range: list[int] | None = None) -> dict[str, Any]:
        guard_path(path)
        return mcp_server.read_text_file(path=path, line_range=line_range)

    def fix_execute_plan(approved_ops: list[dict[str, Any]], workspace_root: str, simulate: bool = False) -> dict[str, Any]:
        if Path(workspace_root).resolve() != workspace:
            raise ValueError(f"workspace_root must be sandbox workspace: {workspace}")
        normalized_ops = _normalize_ops(approved_ops, workspace)
        for i, op in enumerate(normalized_ops):
            for key in ("src", "dst", "path", "dst_dir"):
                value = op.get(key)
                if value:
                    guard_path(value, f"approved_ops[{i}].{key}")
        return mcp_server.fix_execute_plan(
            approved_ops=normalized_ops,
            workspace_root=str(workspace),
            simulate=simulate,
        )

    schemas = [
        _schema_tool(
            "state_tree_read",
            "Read or initialize the markdown workflow state tree for a song. Scope=song (持久,跨 chat 共享).",
            {"song": {"type": "string"}},
            ["song"],
        ),
        _schema_tool(
            "state_tree_update",
            "Update one state checkbox/note in the markdown workflow state tree.",
            {
                "song": {"type": "string"},
                "state_id": {"type": "string"},
                "done": {"type": "boolean"},
                "note": {"type": ["string", "null"]},
            },
            ["song", "state_id", "done"],
        ),
        _schema_tool(
            "sheet_get_song_meta",
            "Fetch sandbox sheet metadata for 1.1. PII fields are masked.",
            {"song_name": {"type": "string"}},
            ["song_name"],
        ),
        _schema_tool("sheet_list_my_pending", "List sandbox pending songs.", {}, []),
        _schema_tool(
            "audit_list_errors",
            "List all QC errors for one sandbox song folder.",
            {"song_path": {"type": "string"}},
            ["song_path"],
        ),
        _schema_tool(
            "fs_list_dir",
            "List a sandbox directory tree.",
            {"path": {"type": "string"}, "max_depth": {"type": "integer", "default": 2}},
            ["path"],
        ),
        _schema_tool(
            "read_text_file",
            "Read a sandbox text file, optionally with line_range=[start,end].",
            {
                "path": {"type": "string"},
                "line_range": {
                    "anyOf": [
                        {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                        {"type": "null"},
                    ]
                },
            },
            ["path"],
        ),
        _schema_tool(
            "fix_execute_plan",
            "Dry-run or execute sandbox file ops. Always call simulate=true before simulate=false with the same ops.",
            {
                "approved_ops": {"type": "array", "items": {"type": "object"}},
                "workspace_root": {"type": "string"},
                "simulate": {"type": "boolean", "default": False},
            },
            ["approved_ops", "workspace_root"],
        ),
    ]
    funcs: dict[str, ToolFn] = {
        "state_tree_read": state_tree_read,
        "state_tree_update": state_tree_update,
        "sheet_get_song_meta": sheet_get_song_meta,
        "sheet_list_my_pending": sheet_list_my_pending,
        "audit_list_errors": audit_list_errors,
        "fs_list_dir": fs_list_dir,
        "read_text_file": read_text_file,
        "fix_execute_plan": fix_execute_plan,
    }
    return schemas, funcs


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls") or []
    if calls:
        return calls
    legacy = message.get("function_call")
    if legacy:
        return [{"id": "legacy_function_call", "type": "function", "function": legacy}]
    return []


def _call_llm(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    from sidecar.config import get_config

    cfg = get_config().test_llm
    if not cfg.endpoint or not cfg.api_key:
        raise RuntimeError("test_llm.endpoint/api_key 未配置")
    payload = {
        "model": cfg.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    url = cfg.endpoint.rstrip("/") + "/v1/chat/completions"
    try:
        with httpx.Client(timeout=120, trust_env=False) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = exc.response.text[:500]
        raise RuntimeError(
            f"test LLM request failed: HTTP {status}. "
            "Check [test_llm].endpoint/api_key in CHECKER_CONFIG. "
            f"Response: {detail}"
        ) from exc
    return data["choices"][0]["message"]


def _system_prompt(workflow_text: str, workspace: Path, song_folder: Path, song_name: str) -> str:
    return f"""你是 Audio QC agent,现在运行在 sandbox 测试模式。

硬约束:
- 只能处理 sandbox workspace: {workspace}
- 当前 song_path: {song_folder}
- 当前 song_name: {song_name}
- 不要访问或推测真实工作区;所有路径都必须在 sandbox workspace 内,调用工具时优先使用工具返回的绝对路径。
- 文件写操作必须先调用 fix_execute_plan(simulate=true),无冲突后才能用同一批 ops 调 simulate=false。
- 如果执行被拒绝或工具返回错误,不要绕过,写 note 或询问用户。
- 当前 runner 没有 human_check/UI 工具。遇到需要人工判断的态,写入 state note 后停止。
- 本次目标:只代测 Part 1 自动检查链路(1.1-1.7 前),尽量完成能确定自动处理的文件问题;WAV 物理格式错误只写 note,不要尝试修音频。
- 每轮最终回复必须包含一行 <summary>...</summary>。

工作手册如下:

{workflow_text}
"""


def run_agent(song_folder: Path, song_name: str, chat_id: str, workspace: Path, max_turns: int) -> int:
    workflow = (ROOT / "doc" / "agent_workflow.md").read_text(encoding="utf-8")
    tools, funcs = _build_tools(workspace)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(workflow, workspace.resolve(), song_folder.resolve(), song_name)},
        {
            "role": "user",
            "content": (
                "开始代测这首 sandbox 歌。请从 state_tree_read 和 1.1 开始,"
                "按工作手册推进 Part 1,能自动修的走 simulate→execute,不能自动修的写 note。"
            ),
        },
    ]

    for turn in range(1, max_turns + 1):
        print(f"\n=== agent turn {turn} ===")
        assistant = _call_llm(messages, tools)
        print(assistant.get("content") or "[tool call]")
        messages.append(assistant)
        tool_calls = _extract_tool_calls(assistant)
        if not tool_calls:
            content = assistant.get("content") or ""
            if "<summary>" in content:
                return 0
            if turn < max_turns:
                messages.append({
                    "role": "user",
                    "content": (
                        "请继续执行，不要只停在分析文字。若当前 state 已判断完成，"
                        "请立刻调用 state_tree_update；若需要检查文件，请调用对应工具。"
                    ),
                })
                continue
            return 0

        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name")
            raw_args = fn.get("arguments") or "{}"
            call_id = call.get("id") or name or "tool_call"
            print(f"\n--- tool: {name} ---")
            print(raw_args)
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                if name not in funcs:
                    raise ValueError(f"unknown tool: {name}")
                result = funcs[name](**args)
            except Exception as exc:  # noqa: BLE001 - dev runner surfaces tool errors to model
                result = {"ok": False, "code": "TOOL_EXCEPTION", "message": str(exc)}
            rendered = _compact_tool_result(result)
            print(rendered)
            messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": rendered})

    print(f"Reached max_turns={max_turns} before final response.", file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--song-folder", default="歌手_Agent测试_扒谱者")
    parser.add_argument("--song-name", default="Agent测试")
    parser.add_argument("--chat-id", default="agent-sandbox-test")
    parser.add_argument("--max-turns", type=int, default=20)
    args = parser.parse_args()

    _ensure_config_env(args.config)
    from sidecar import config
    from sidecar import workspace as _ws

    config.reload_config()
    workspace = args.workspace.resolve()
    # 让 sidecar 也知道 sandbox workspace,以便 audit/read/fs_list_dir 的相对路径解析
    _ws.set_workspace(str(workspace))
    song_folder = (workspace / args.song_folder).resolve()
    if not _is_within(song_folder, workspace):
        raise SystemExit(f"song folder outside workspace: {song_folder}")
    if not song_folder.is_dir():
        raise SystemExit(f"song folder missing: {song_folder}")
    return run_agent(song_folder, args.song_name, args.chat_id, workspace, args.max_turns)


if __name__ == "__main__":
    raise SystemExit(main())
