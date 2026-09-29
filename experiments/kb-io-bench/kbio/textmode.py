"""v2 text tool-call mode (V2-PLAN §6): for chat models without native tool calls.

`text_chat(chat)` wraps any `ChatFn`, so `run_agent` is unchanged:
- the tool list goes into the system prompt, with a fixed calling convention: one fenced block
  ```tool {"tool": ..., "arguments": {...}}```;
- past tool calls are rendered back as those blocks, and tool results as user turns;
- a reply's blocks are parsed into OpenAI-style `tool_calls` with deterministic ids.
When `tools` is None (a forced final answer), the catalogue is left out and nothing is parsed.
`pick_tool_mode` is the per-model probe: native if a tiny request yields a native call, else text
if the text convention is followed, else none (the model is excluded, with the reason recorded).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from kbio.agent import MALFORMED

ChatFn = Callable[..., dict[str, Any]]

PROTOCOL = """You can call tools. To call a tool, end your reply with one fenced block like this:
```tool
{{"tool": "<tool name>", "arguments": {{<arguments as JSON>}}}}
```
Then stop; the result comes back in the next message. Call one tool at a time. When you have your
final answer, reply without any tool block.

Tools:
{catalogue}"""
# makes run_agent treat the turn as a malformed call (it checks for harmony markers); stripped
# again when the history is rendered for the model
MALFORMED_TAG = "\n<|call|>"
TEXT_MALFORMED = (
    "Your last tool block could not be parsed. Reply again with one ```tool block holding a JSON "
    'object {"tool": ..., "arguments": {...}}, or give your final answer.'
)
RESULT = "Result of tool `{name}`:\n{content}"
BLOCK = re.compile(r"```[ \t]*(?:tool|json|tool_call)?[ \t]*\n(.*?)\n?```", re.S)


def catalogue(tools: list[dict[str, Any]]) -> str:
    out = []
    for t in tools:
        fn = t.get("function", t)
        params = json.dumps(fn.get("parameters", {}), ensure_ascii=False, sort_keys=True)
        out.append(f"- {fn['name']}: {fn.get('description', '')}\n  parameters: {params}")
    return "\n".join(out)


def render_call(call: dict[str, Any]) -> str:
    fn = call.get("function", {})
    raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        args = {"_raw": raw}
    return "```tool\n" + json.dumps({"tool": fn.get("name", ""), "arguments": args},
                                     ensure_ascii=False) + "\n```"  # fmt: skip


def render_messages(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """OpenAI tool-calling history -> plain system/user/assistant turns."""
    names: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system" and tools:
            out.append({"role": "system", "content": m["content"] + "\n\n"
                        + PROTOCOL.format(catalogue=catalogue(tools))})  # fmt: skip
        elif role == "assistant" and m.get("tool_calls"):
            for c in m["tool_calls"]:
                names[c.get("id", "")] = c.get("function", {}).get("name", "")
            text = "\n".join([m.get("content") or ""] + [render_call(c) for c in m["tool_calls"]])
            out.append({"role": "assistant", "content": text.strip()})
        elif role == "tool":
            body = RESULT.format(
                name=names.get(m.get("tool_call_id", ""), "?"), content=m["content"]
            )
            if out and out[-1]["role"] == "user" and out[-1].get("_results"):
                out[-1]["content"] += "\n\n" + body
            else:
                out.append({"role": "user", "content": body, "_results": True})
        else:
            content = (m.get("content") or "").removesuffix(MALFORMED_TAG)
            if role == "user" and content == MALFORMED:
                content = TEXT_MALFORMED  # there is no tool-calling interface in text mode
            out.append({"role": role, "content": content})
    for m in out:
        m.pop("_results", None)
    return out


def _as_call(obj: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("tool", obj.get("name"))
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if not isinstance(name, str) or not name:
        return None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    return (name, args) if isinstance(args, dict) else None


def parse_calls(content: str) -> tuple[str, list[tuple[str, dict[str, Any]]], bool]:
    """(text before the first block, parsed calls, saw an unparseable tool block)."""
    calls: list[tuple[str, dict[str, Any]]] = []
    bad = False
    blocks = list(BLOCK.finditer(content))
    for b in blocks:
        try:
            c = _as_call(json.loads(b.group(1)))
        except json.JSONDecodeError:
            c = None
        if c:
            calls.append(c)
        elif b.group(0).startswith("```tool"):
            bad = True
    if not blocks:
        s = content.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                c = _as_call(json.loads(s))
            except json.JSONDecodeError:
                c = None
            if c:
                return "", [c], False
        return content, [], False
    prefix = content[: blocks[0].start()].strip() if calls else content
    return prefix, calls, bad


def text_chat(inner: ChatFn) -> ChatFn:
    step = {"n": 0}

    def chat(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             max_tokens: int = 1500, **kw: Any) -> dict[str, Any]:  # fmt: skip
        resp = inner(
            model, render_messages(messages, tools), tools=None, max_tokens=max_tokens, **kw
        )
        msg = dict(resp["message"])
        content = msg.get("content") or ""
        if tools:
            prefix, calls, bad = parse_calls(content)
            if calls:
                msg["content"] = prefix
                msg["tool_calls"] = [
                    {
                        "id": f"t{step['n']}-{i}",
                        "type": "function",
                        "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)},
                    }
                    for i, (n, a) in enumerate(calls)
                ]
            elif bad:
                # an unparseable tool block: run_agent answers harmony-marked content as malformed
                msg["content"] = content + MALFORMED_TAG
        step["n"] += 1
        return {**resp, "message": msg, "tool_mode": "text"}

    return chat


PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup",
        "description": "Look up a code word in the registry.",
        "parameters": {
            "type": "object",
            "properties": {"word": {"type": "string"}},
            "required": ["word"],
        },  # fmt: skip
    },
}
PROBE_USER = "Use the lookup tool to look up the code word 'amber'. Do not answer without it."


def pick_tool_mode(model: str, chat: ChatFn) -> tuple[str, str]:
    """(mode, reason): 'native', 'text' or 'none'."""
    msgs = [{"role": "system", "content": "You are a careful assistant."},
            {"role": "user", "content": PROBE_USER}]  # fmt: skip
    r = chat(model, msgs, tools=[PROBE_TOOL], max_tokens=300)
    calls = r["message"].get("tool_calls") or []
    if any(c.get("function", {}).get("name") == "lookup" for c in calls):
        return "native", "native tool call on the probe"
    native_text = (r["message"].get("content") or "")[:200]
    r2 = text_chat(chat)(model, msgs, tools=[PROBE_TOOL], max_tokens=300)
    calls = r2["message"].get("tool_calls") or []
    if any(c["function"]["name"] == "lookup" for c in calls):
        return "text", f"no native call (replied {native_text!r}); text convention followed"
    return "none", f"no native call and no parseable text call ({native_text!r})"
