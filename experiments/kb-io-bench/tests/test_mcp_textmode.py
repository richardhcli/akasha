"""V2 pieces that need no scratch daemon: the manifest-driven MCP harness (on crud-kb) and the text
tool-call mode, both driven through the unchanged v1 agent loop with fake models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kbio.agent import run_agent
from kbio.harnesses.mcp import McpHarness, load_manifest
from kbio.textmode import (
    MALFORMED_TAG,
    PROBE_TOOL,
    TEXT_MALFORMED,
    parse_calls,
    pick_tool_mode,
    render_messages,
    text_chat,
)


def make_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "Wikipedia").mkdir(parents=True)
    (kb / "Wikipedia" / "Habit.md").write_text("# Habit\n\nA habit is a routine. Entry 41-C.\n")
    (kb / "Habits.md").write_text("# Habits\n\nSee [[Wikipedia/Habit]].\n")
    return kb


def fake(script: list[dict[str, Any]]):
    seen: list[dict[str, Any]] = []

    def chat(model, messages, tools=None, max_tokens=0):  # noqa: ANN001
        seen.append({"messages": [dict(m) for m in messages], "tools": tools})
        return {"message": script[len(seen) - 1],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1}, "latency_ms": 1.0}  # fmt: skip

    return chat, seen


def tc(name: str, args: dict[str, Any], i: int) -> dict[str, Any]:
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}  # fmt: skip


# ---------------------------------------------------------------- manifest-driven MCP harness
def test_manifest_loads() -> None:
    m = load_manifest("crud-kb")
    assert m.read == ["search", "read", "list"] and m.write == ["create", "update", "delete"]
    assert m.ingest and "{store}/kb.db" in m.ingest


def test_read_only_allowlist_and_ingest(tmp_path: Path) -> None:
    h = McpHarness(load_manifest("crud-kb"), write_tools=False)
    tools = h.setup(make_kb(tmp_path), tmp_path / "scratch")
    try:
        assert [t.name for t in tools] == ["search", "read", "list"]
        assert h.ingest_stats["records"] == 2 and h.ingest_stats["wall_s"] >= 0
        hits = json.loads(h.call("search", {"query": "41-C"}))
        assert hits[0]["id"] == "Wikipedia/Habit"
        with pytest.raises(ValueError, match="unknown tool create"):
            h.call("create", {"title": "x", "body": "y"})
    finally:
        h.teardown()


def test_scripted_crud_sequence(tmp_path: Path) -> None:
    """The V2 cross-harness check, on crud-kb: C, R, U, D each observable by search/read."""
    h = McpHarness(load_manifest("crud-kb"))
    tools = h.setup(make_kb(tmp_path), tmp_path / "scratch")
    try:
        assert [t.name for t in tools] == ["search", "read", "list", "create", "update", "delete"]
        new = json.loads(h.call("create", {"title": "Marlow", "body": "Habit is entry 55-Q."}))[
            "id"
        ]
        assert json.loads(h.call("search", {"query": "55-Q"}))[0]["id"] == new
        h.call("update", {"id": new, "old": "55-Q", "new": "17-B"})
        assert "17-B" in h.call("read", {"id": new})
        assert json.loads(h.call("search", {"query": "55-Q"})) == []
        h.call("delete", {"id": new})
        assert json.loads(h.call("search", {"query": "17-B"})) == []
        with pytest.raises(RuntimeError, match="no document"):
            h.call("read", {"id": new})
    finally:
        h.teardown()


# ---------------------------------------------------------------- text tool-call mode
def test_parse_calls_variants() -> None:
    txt = 'I will search.\n```tool\n{"tool": "search", "arguments": {"query": "41-C"}}\n```'
    assert parse_calls(txt) == ("I will search.", [("search", {"query": "41-C"})], False)
    assert parse_calls('```json\n{"name": "read", "parameters": {"id": "x"}}\n```')[1] == [
        ("read", {"id": "x"})
    ]
    assert parse_calls('{"tool": "list", "arguments": "{}"}')[1] == [("list", {})]
    assert parse_calls("The answer is 41-C.") == ("The answer is 41-C.", [], False)
    assert parse_calls('```json\n{"answer": 1}\n```')[1:] == ([], False)  # JSON, not a call
    assert parse_calls("```tool\n{broken\n```")[1:] == ([], True)


def test_render_messages_round_trip() -> None:
    tools = [PROBE_TOOL]
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "Q"},
        {"role": "assistant", "content": "thinking", "tool_calls": [
            {"id": "a", "function": {"name": "lookup", "arguments": '{"word": "amber"}'}},
            {"id": "b", "function": {"name": "lookup", "arguments": '{"word": "jade"}'}}]},
        {"role": "tool", "tool_call_id": "a", "content": "R1"},
        {"role": "tool", "tool_call_id": "b", "content": "R2"},
        {"role": "assistant", "content": "oops" + MALFORMED_TAG},
    ]  # fmt: skip
    out = render_messages(msgs, tools)
    assert (
        out[0]["content"].startswith("SYS\n\nYou can call tools.")
        and "- lookup:" in out[0]["content"]
    )
    assert out[2] == {
        "role": "assistant",
        "content": 'thinking\n```tool\n{"tool": "lookup", "arguments": {"word": "amber"}}\n```\n```tool\n{"tool": "lookup", "arguments": {"word": "jade"}}\n```',
    }
    assert out[3] == {
        "role": "user",
        "content": "Result of tool `lookup`:\nR1\n\nResult of tool `lookup`:\nR2",
    }
    assert out[4]["content"] == "oops" and all("tool_calls" not in m for m in out)
    assert render_messages(msgs[:2], None)[0]["content"] == "SYS"  # forced final: no catalogue


def test_text_mode_agent_over_crud_kb(tmp_path: Path) -> None:
    h = McpHarness(load_manifest("crud-kb"), write_tools=False)
    tools = h.setup(make_kb(tmp_path), tmp_path / "scratch")
    inner, seen = fake([
        {"role": "assistant", "content": '```tool\n{"tool": "search", "arguments": {"query": "41-C"}}\n```'},
        {"role": "assistant", "content": "```tool\n{not json\n```"},
        {"role": "assistant", "content": 'Reading.\n```tool\n{"tool": "read", "arguments": {"id": "Wikipedia/Habit"}}\n```'},
        {"role": "assistant", "content": 'Entry 41-C.\nSOURCES:\n- Wikipedia/Habit: "Entry 41-C."'},
    ])  # fmt: skip
    try:
        out = run_agent("llama", "sys", "q?", h, tools, max_steps=12, chat=text_chat(inner))
    finally:
        h.teardown()
    assert out["status"] == "ok" and out["final"].startswith("Entry 41-C.")
    assert out["tool_calls"] == 2 and out["malformed_calls"] == 1
    assert all(s["tools"] is None for s in seen)  # the endpoint never gets native tools
    last = seen[-1]["messages"]
    assert "- search:" in last[0]["content"]
    assert all(m["role"] in ("system", "user", "assistant") for m in last)
    assert any(
        m["role"] == "user" and m["content"].startswith("Result of tool `read`:") for m in last
    )
    assert not any(MALFORMED_TAG in m["content"] for m in last)
    assert any(m["content"] == TEXT_MALFORMED for m in last)  # not "tool-calling interface"
    assert not any("tool-calling interface" in m["content"] for m in last)


def test_pick_tool_mode() -> None:
    native, _ = fake([tc("lookup", {"word": "amber"}, 0)])
    assert pick_tool_mode("m", native)[0] == "native"
    as_text = '```tool\n{"tool": "lookup", "arguments": {"word": "amber"}}\n```'
    txt, _ = fake([{"role": "assistant", "content": '{"type": "function", "name": "lookup"'},
                   {"role": "assistant", "content": as_text}])  # fmt: skip
    mode, why = pick_tool_mode("m", txt)
    assert mode == "text" and "no native call" in why
    none, _ = fake([{"role": "assistant", "content": "amber"}] * 2)
    assert pick_tool_mode("m", none)[0] == "none"
