"""Tests for the sandbox agent runner guardrails."""

from pathlib import Path

import pytest

from scripts import run_agent_sandbox


def test_is_within_accepts_child(tmp_path):
    root = tmp_path / "workspace"
    child = root / "song" / "x.txt"
    child.parent.mkdir(parents=True)
    child.write_text("x", encoding="utf-8")
    assert run_agent_sandbox._is_within(child, root)


def test_is_within_rejects_sibling(tmp_path):
    root = tmp_path / "workspace"
    sibling = tmp_path / "outside.txt"
    root.mkdir()
    sibling.write_text("x", encoding="utf-8")
    assert not run_agent_sandbox._is_within(sibling, root)


def test_build_tools_rejects_outside_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _, funcs = run_agent_sandbox._build_tools(workspace)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError):
        funcs["read_text_file"](**{"path": str(outside)})


def test_build_tools_rejects_wrong_workspace_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _, funcs = run_agent_sandbox._build_tools(workspace)
    with pytest.raises(ValueError):
        funcs["fix_execute_plan"](
            approved_ops=[],
            workspace_root=str(tmp_path),
            simulate=True,
        )


def test_tool_schemas_include_required_runner_tools(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    schemas, funcs = run_agent_sandbox._build_tools(workspace)
    names = {s["function"]["name"] for s in schemas}
    assert {
        "state_tree_read",
        "sheet_get_song_meta",
        "audit_list_errors",
        "fix_execute_plan",
    }.issubset(names)
    assert names == set(funcs)
