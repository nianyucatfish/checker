"""LLM provider 协议适配。

agent(Electron 侧)永远只说 OpenAI Chat Completions 形状;各 provider 的协议差异全在这里翻译,
api.py /agent/completion 按 cfg.protocol 分发。纯函数,便于 pytest 单测。

- protocol="openai":几乎所有厂商(OpenAI / DeepSeek / Moonshot / 智谱 / OpenRouter / Gemini 兼容端点 / Ollama …)
- protocol="anthropic":Anthropic 原生 Messages API(/v1/messages,x-api-key,system 顶层,tool_use/tool_result blocks)
"""

from __future__ import annotations

import json
from typing import Any


# ---------- OpenAI 兼容 ----------

def openai_endpoint_url(endpoint: str) -> str:
    """endpoint 可填完整 chat URL,或 base(自动补 /v1/chat/completions)。"""
    e = endpoint.rstrip("/")
    return e if e.endswith("/chat/completions") else e + "/v1/chat/completions"


def openai_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


# ---------- Anthropic 原生 ----------

def anthropic_url(endpoint: str) -> str:
    e = endpoint.rstrip("/")
    return e if e.endswith("/v1/messages") else e + "/v1/messages"


def anthropic_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }


def to_anthropic_request(body: dict, model: str, max_tokens: int) -> dict:
    """OpenAI Chat Completions body → Anthropic Messages body。

    关键转换:
    - system 角色消息抽到顶层 system 参数(Anthropic 的 messages 里没有 system 角色)
    - assistant.tool_calls → content blocks {type:tool_use, id, name, input}
    - role:tool 结果 → 折进一条 user 消息的 {type:tool_result, tool_use_id, content};
      连续的 tool 结果合并进同一条 user 消息(对齐前一轮 assistant 的多个 tool_use)
    - 空 content 的 assistant(纯工具轮)补占位 text,Anthropic 不接受空 content
    """
    system_parts: list[str] = []
    out_messages: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            out_messages.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for m in body.get("messages", []):
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
            continue
        if role == "tool":
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "content": m.get("content") or "",
            })
            continue
        flush_tool_results()  # user/assistant 之前先收掉累积的 tool 结果
        if role == "user":
            out_messages.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    inp = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": inp,
                })
            out_messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "."}]})
    flush_tool_results()

    out: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": out_messages}
    if system_parts:
        out["system"] = "\n\n".join(system_parts)

    tools = []
    for t in body.get("tools") or []:
        fn = t.get("function", {})
        tools.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    if tools:
        out["tools"] = tools
        out["tool_choice"] = {"type": "auto"}
    return out


_STOP_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
}


def from_anthropic_response(data: dict) -> dict:
    """Anthropic Messages 响应 → agent 期望的 OpenAI 形状。

    Returns {message:{role,content,tool_calls?}, finish_reason, usage:{prompt_tokens,completion_tokens}, cached_tokens}
    """
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    tool_calls = [
        {
            "id": b.get("id", ""),
            "type": "function",
            "function": {
                "name": b.get("name", ""),
                "arguments": json.dumps(b.get("input", {}), ensure_ascii=False),
            },
        }
        for b in blocks
        if b.get("type") == "tool_use"
    ]
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = data.get("usage", {}) or {}
    return {
        "message": message,
        "finish_reason": _STOP_MAP.get(data.get("stop_reason", ""), data.get("stop_reason", "")),
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        },
        "cached_tokens": usage.get("cache_read_input_tokens", 0) or 0,
    }
