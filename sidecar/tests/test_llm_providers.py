"""LLM provider 协议转换的单测(纯函数)。"""
import json

from sidecar import llm_providers as llm


def test_openai_endpoint_url():
    assert llm.openai_endpoint_url("https://api.deepseek.com") == "https://api.deepseek.com/v1/chat/completions"
    assert llm.openai_endpoint_url("https://api.deepseek.com/") == "https://api.deepseek.com/v1/chat/completions"
    # 已是完整 chat URL(如 Gemini 兼容端点)→ 原样
    full = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert llm.openai_endpoint_url(full) == full


def test_anthropic_url_and_headers():
    assert llm.anthropic_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"
    assert llm.anthropic_url("https://api.anthropic.com/v1/messages") == "https://api.anthropic.com/v1/messages"
    h = llm.anthropic_headers("sk-ant-xxx")
    assert h["x-api-key"] == "sk-ant-xxx"
    assert h["anthropic-version"] == "2023-06-01"


def test_to_anthropic_system_extraction():
    body = {"messages": [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "hi"},
    ]}
    out = llm.to_anthropic_request(body, "claude-x", 4096)
    assert out["system"] == "你是助手"
    assert out["max_tokens"] == 4096
    assert out["messages"] == [{"role": "user", "content": "hi"}]
    assert "system" not in [m["role"] for m in out["messages"]]


def test_to_anthropic_assistant_tool_calls():
    body = {"messages": [
        {"role": "user", "content": "查一下"},
        {"role": "assistant", "content": "好的", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": '{"x":1}'}},
        ]},
    ]}
    out = llm.to_anthropic_request(body, "claude-x", 100)
    asst = out["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["content"][0] == {"type": "text", "text": "好的"}
    assert asst["content"][1] == {"type": "tool_use", "id": "c1", "name": "f", "input": {"x": 1}}


def test_to_anthropic_tool_results_merged_into_one_user_msg():
    # 一轮 assistant 两个 tool_call → 两条 tool 结果要合并进同一条 user 消息
    body = {"messages": [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "tool", "tool_call_id": "c2", "content": "r2"},
        {"role": "user", "content": "继续"},
    ]}
    out = llm.to_anthropic_request(body, "claude-x", 100)
    # [0] assistant(纯 tool_use,补占位 text? 否,有 tool_use 就不补) [1] user(两个 tool_result) [2] user 继续
    assert out["messages"][0]["role"] == "assistant"
    tr_msg = out["messages"][1]
    assert tr_msg["role"] == "user"
    assert [b["tool_use_id"] for b in tr_msg["content"]] == ["c1", "c2"]
    assert [b["content"] for b in tr_msg["content"]] == ["r1", "r2"]
    assert out["messages"][2] == {"role": "user", "content": "继续"}


def test_to_anthropic_empty_assistant_gets_placeholder():
    body = {"messages": [{"role": "assistant", "content": "", "tool_calls": []}]}
    out = llm.to_anthropic_request(body, "claude-x", 100)
    assert out["messages"][0]["content"] == [{"type": "text", "text": "."}]


def test_to_anthropic_tools_schema():
    body = {"messages": [], "tools": [
        {"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object", "properties": {"a": {"type": "string"}}}}},
    ]}
    out = llm.to_anthropic_request(body, "claude-x", 100)
    assert out["tools"][0] == {"name": "f", "description": "d", "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}}}
    assert out["tool_choice"] == {"type": "auto"}


def test_from_anthropic_text_and_tool_use():
    data = {
        "content": [
            {"type": "text", "text": "好的,"},
            {"type": "text", "text": "我查一下"},
            {"type": "tool_use", "id": "tu1", "name": "audit", "input": {"song": "x"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 473, "output_tokens": 65, "cache_read_input_tokens": 100},
    }
    out = llm.from_anthropic_response(data)
    assert out["message"]["content"] == "好的,我查一下"
    assert out["message"]["tool_calls"][0]["id"] == "tu1"
    assert out["message"]["tool_calls"][0]["function"]["name"] == "audit"
    assert json.loads(out["message"]["tool_calls"][0]["function"]["arguments"]) == {"song": "x"}
    assert out["finish_reason"] == "tool_calls"
    assert out["usage"] == {"prompt_tokens": 473, "completion_tokens": 65}
    assert out["cached_tokens"] == 100


def test_from_anthropic_stop_reason_map():
    assert llm.from_anthropic_response({"content": [], "stop_reason": "end_turn"})["finish_reason"] == "stop"
    assert llm.from_anthropic_response({"content": [], "stop_reason": "max_tokens"})["finish_reason"] == "length"


def test_from_anthropic_text_only_no_tool_calls_key():
    out = llm.from_anthropic_response({"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"})
    assert out["message"]["content"] == "hi"
    assert "tool_calls" not in out["message"]
