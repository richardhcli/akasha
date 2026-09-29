"""Agent loop + files/closed-book harnesses against a scripted fake model (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kbio.agent import run_agent, truncate
from kbio.harnesses.closedbook import ClosedBookHarness
from kbio.harnesses.files import FilesHarness


def fake_chat(script: list[dict[str, Any]]):
    calls: list[dict[str, Any]] = []

    def chat(model, messages, tools=None, max_tokens=0):  # noqa: ANN001
        calls.append({"messages": list(messages), "tools": tools})
        step = script[len(calls) - 1]
        return {
            "message": step,
            "usage": {"prompt_tokens": 100 * len(calls), "completion_tokens": 10},
            "latency_ms": 5.0,
        }

    return chat, calls


def tool_call(name: str, args: dict[str, Any], i: int = 0) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"c{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def make_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "Wikipedia").mkdir(parents=True)
    (kb / "Wikipedia" / "Habit.md").write_text("# Habit\n\nA habit is a routine.\n\nEntry 41-C.\n")
    (kb / "Habits.md").write_text("# Habits\n\n- Wikipedia page: [[Wikipedia/Habit]]\n")
    return kb


def test_read_loop_with_tools(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    h = FilesHarness()
    tools = h.setup(kb, tmp_path)
    chat, calls = fake_chat(
        [
            tool_call("grep", {"pattern": "41-C"}, 0),
            tool_call("read_file", {"path": "Wikipedia/Habit.md"}, 1),
            {
                "role": "assistant",
                "content": 'Entry 41-C.\nSOURCES:\n- Wikipedia/Habit.md: "Entry 41-C."',
            },
        ]
    )
    out = run_agent("fake", "sys", "q?", h, tools, max_steps=12, chat=chat)
    assert out["status"] == "ok"
    assert out["final"].startswith("Entry 41-C.")
    assert out["tool_calls"] == 2 and out["steps"] == 3
    assert out["prompt_tokens"] == 100 + 200 + 300 and out["completion_tokens"] == 30
    grep_res = out["turns"][0]["tool_calls"][0]["result"]
    assert "Wikipedia/Habit.md:5: Entry 41-C." in grep_res
    # the tool result is fed back as a tool message with the call id
    assert calls[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c0", "content": grep_res}


def test_step_cap_forces_final_answer(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    h = FilesHarness()
    tools = h.setup(kb, tmp_path)
    script = [tool_call("list_dir", {}, i) for i in range(2)] + [
        {"role": "assistant", "content": "NOT FOUND IN KNOWLEDGE BASE"}
    ]
    chat, calls = fake_chat(script)
    out = run_agent("fake", "sys", "q?", h, tools, max_steps=2, chat=chat)
    assert out["hit_step_cap"] and out["final"] == "NOT FOUND IN KNOWLEDGE BASE"
    assert calls[-1]["tools"] is None  # forced final turn offers no tools


def test_empty_final_is_reasked_once(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    h = FilesHarness()
    tools = h.setup(kb, tmp_path)
    script = [tool_call("list_dir", {}, i) for i in range(2)] + [
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "NOT FOUND IN KNOWLEDGE BASE"},
    ]
    chat, calls = fake_chat(script)
    out = run_agent("fake", "sys", "q?", h, tools, max_steps=2, chat=chat)
    assert out["final"] == "NOT FOUND IN KNOWLEDGE BASE" and out["reasked_empty"]
    assert out["hit_step_cap"] and out["steps"] == 4 and calls[-1]["tools"] is None
    # the budget message is sent once; the re-ask follows the empty reply
    users = [m["content"] for m in calls[-1]["messages"] if m["role"] == "user"]
    assert len(users) == 3 and users[-1].startswith("Your last reply was empty")
    # a second empty reply ends the run (at most one re-ask)
    chat2, calls2 = fake_chat([{"role": "assistant", "content": ""}] * 2)
    out2 = run_agent("fake", "sys", "q?", h, tools, max_steps=12, chat=chat2)
    assert out2["final"] == "" and len(calls2) == 2 and not out2["hit_step_cap"]


def test_budget_warning_is_sent_once_before_the_cap(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    h = FilesHarness()
    tools = h.setup(kb, tmp_path)
    script = [tool_call("list_dir", {}, i) for i in range(5)] + [
        {"role": "assistant", "content": "DONE"}
    ]
    chat, calls = fake_chat(script)
    out = run_agent("fake", "sys", "q?", h, tools, max_steps=5, chat=chat, warn_left=2)
    assert out["final"] == "DONE"
    warned = [
        i
        for i, c in enumerate(calls)
        if any("tool calls left" in (m.get("content") or "") for m in c["messages"])
    ]
    assert warned and warned[0] == 3  # after 3 of 5 steps: 2 left
    users = [m["content"] for m in calls[-1]["messages"] if m["role"] == "user"]
    assert sum("tool calls left" in u for u in users) == 1
    # off by default (READ requests unchanged)
    chat2, calls2 = fake_chat(script)
    run_agent("fake", "sys", "q?", h, tools, max_steps=5, chat=chat2)
    assert not any("tool calls left" in (m.get("content") or "") for m in calls2[-1]["messages"])


def test_reasoning_is_carried_as_content_of_tool_call_turns(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    h = FilesHarness()
    tools = h.setup(kb, tmp_path)
    first = {**tool_call("list_dir", {}, 0), "reasoning": "Plan: list, then grep 41-C."}
    chat, calls = fake_chat([first, {"role": "assistant", "content": "done"}])
    out = run_agent("fake", "sys", "q?", h, tools, max_steps=5, chat=chat)
    sent = calls[1]["messages"][2]
    assert sent["role"] == "assistant" and sent["content"] == "Plan: list, then grep 41-C."
    assert "reasoning" not in sent and out["turns"][0]["reasoning"].startswith("Plan")


def test_leaked_harmony_call_is_malformed_not_final(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    h = FilesHarness()
    tools = h.setup(kb, tmp_path)
    leak = "<|start|>assistant<|channel|>commentary to=functions.grep<|call|>"
    chat, calls = fake_chat(
        [{"role": "assistant", "content": leak}, {"role": "assistant", "content": "DONE"}]
    )
    out = run_agent("fake", "sys", "q?", h, tools, max_steps=5, chat=chat)
    assert out["final"] == "DONE" and out["malformed_calls"] == 1 and out["steps"] == 2
    assert calls[1]["tools"] is not None
    assert calls[1]["messages"][-1]["content"].startswith("Your last message was a malformed")


def test_bad_tool_call_is_reported_not_raised(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    h = FilesHarness()
    tools = h.setup(kb, tmp_path)
    chat, _ = fake_chat(
        [
            tool_call("read_file", {"path": "../../etc/passwd"}, 0),
            tool_call("nope", {}, 1),
            {"role": "assistant", "content": "done"},
        ]
    )
    out = run_agent("fake", "sys", "q?", h, tools, max_steps=12, chat=chat)
    results = [t["tool_calls"][0]["result"] for t in out["turns"][:2]]
    assert results[0].startswith("ERROR: ValueError: path escapes")
    assert results[1].startswith("ERROR: ValueError: unknown tool")


def test_write_and_edit(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    h = FilesHarness()
    h.setup(kb, tmp_path)
    h.call("edit_file", {"path": "Wikipedia/Habit.md", "old": "41-C", "new": "17-B"})
    assert "17-B" in (kb / "Wikipedia" / "Habit.md").read_text()
    h.call("write_file", {"path": "New/Memo.md", "content": "x"})
    assert (kb / "New" / "Memo.md").read_text() == "x"


def test_closed_book_has_no_tools(tmp_path: Path) -> None:
    h = ClosedBookHarness()
    assert h.setup(tmp_path, tmp_path) == []
    chat, calls = fake_chat([{"role": "assistant", "content": "1874"}])
    out = run_agent("fake", "sys", "q?", h, [], max_steps=12, chat=chat)
    assert out["final"] == "1874" and calls[0]["tools"] is None


def test_truncate_marks_cut() -> None:
    text = "word " * 5000
    out = truncate(text, 100)
    assert "[... truncated:" in out and len(out) < len(text)


def test_grep_shows_window_around_match_in_long_lines(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("x" * 2000 + " NEEDLE 2007 " + "y" * 2000 + "\n")
    h = FilesHarness()
    h.setup(kb, tmp_path)
    out = h.call("grep", {"pattern": "needle"})
    assert "NEEDLE 2007" in out and len(out) < 600


def test_parse_model_window() -> None:
    from kbio.agent import parse_model

    assert parse_model("gpt-oss:120b") == ("gpt-oss:120b", None)
    assert parse_model("gpt-oss:120b@ctx8k") == ("gpt-oss:120b", 8192)


def test_context_window_evicts_oldest_tool_results(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    (kb / "Big.md").write_text("# Big\n\n" + "filler word " * 1500 + "\n")  # ~3k tokens
    h = FilesHarness()
    tools = h.setup(kb, tmp_path)
    script = [
        tool_call("read_file", {"path": "Big.md"}, 0),
        tool_call("read_file", {"path": "Big.md"}, 1),
        tool_call("grep", {"pattern": "41-C"}, 2),
        {"role": "assistant", "content": "Entry 41-C."},
    ]
    chat, calls = fake_chat(script)
    base = run_agent("fake", "sys", "q?", h, tools, max_steps=12, chat=chat)
    chat2, calls2 = fake_chat(script)
    out = run_agent("fake@ctx6k", "sys", "q?", h, tools, max_steps=12, chat=chat2)
    assert base["status"] == out["status"] == "ok" and out["final"] == "Entry 41-C."
    assert "evictions" not in base  # unwindowed records are unchanged
    assert out["context_window"] == 6144 and out["evictions"] == 1
    # requests before the first eviction are identical to the unwindowed run (cache replay)
    assert calls2[0]["messages"] == calls[0]["messages"]
    assert calls2[1]["messages"] == calls[1]["messages"]
    last = calls2[3]["messages"]
    tool_msgs = [m for m in last if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c0", "c1", "c2"]
    assert tool_msgs[0]["content"] == "[evicted to fit the context window]"
    assert tool_msgs[1]["content"] != tool_msgs[0]["content"]  # the newest results are kept
    assert last[:2] == calls[0]["messages"][:2]  # system prompt and task are never evicted
    assert [t["evicted"] for t in out["turns"]] == [0, 0, 1, 0]


def test_context_window_overflow_ends_run(tmp_path: Path) -> None:
    kb = make_kb(tmp_path)
    (kb / "Big.md").write_text("# Big\n\n" + "filler word " * 1500 + "\n")
    h = FilesHarness()
    tools = h.setup(kb, tmp_path)
    chat, _ = fake_chat([tool_call("read_file", {"path": "Big.md"}, 0)])
    out = run_agent("fake@ctx4k", "sys", "q?", h, tools, max_steps=12, chat=chat)
    assert out["status"] == "context_overflow" and out["final"] == ""
    assert out["steps"] == 1


def test_files_harness_tolerates_dropped_number_prefix(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    (kb / "(1) Universal").mkdir(parents=True)
    (kb / "(1) Universal" / "Truth.md").write_text("# Truth\n\nOld line.\n")
    h = FilesHarness()
    h.setup(kb, tmp_path)
    assert "Old line." in h.call("read_file", {"path": "Universal/Truth.md"})
    assert h.call("list_dir", {"path": "Universal"}) == "(1) Universal/Truth.md"
    h.call("edit_file", {"path": "Universal/Truth.md", "old": "Old", "new": "New"})
    assert (kb / "(1) Universal" / "Truth.md").read_text() == "# Truth\n\nNew line.\n"
    assert not (kb / "Universal").exists()
    h.call("write_file", {"path": "Universal/New.md", "content": "x"})
    assert (kb / "(1) Universal" / "New.md").read_text() == "x"
