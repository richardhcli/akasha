"""One agent loop for every condition: OpenAI-style native tool calls against Purdue.

Identical across conditions: model, system prompt skeleton, temperature 0, step caps (12 READ,
20 WRITE/UPDATE), tool results capped at 2,000 cl100k tokens with a marker. Every turn records
prompt/completion tokens (API `usage`, tiktoken fallback), tool names/arguments, result sizes and
latency. Tokens are summed over all turns.

Loop v2 (2026-09-27): the gateway drops reasoning passed back in any field, so gpt-oss re-planned
from scratch every step and looped. The reasoning of a tool-call turn is now carried back as that
turn's visible content (same rule in every condition; empty for models that return none). A raw
harmony tool call leaked into content is answered as a malformed call, not taken as the final.

Context window (M11): a model label `gpt-oss:120b@ctx8k` runs the same model with a client-side
window of 8,192 tokens (prompt + max_tokens). Before each request that would not fit, the oldest
tool results, then the oldest carried reasoning, are replaced by a placeholder (OpenAI
`truncation: "auto"`, Anthropic clear-tool-results context editing). The system prompt, the task
and the newest tool results are kept; if it still does not fit, the run ends as `context_overflow`.
Until the first eviction the request body is identical to the unwindowed run, so it replays from
the response cache."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from kbio import llm

MAX_RESULT_TOKENS = 2000
STEPS = {"read": 12, "write": 20, "update": 20}
EMPTY_REASK = (
    "Your last reply was empty. Give your final answer now, in the required format, "
    "without calling tools."
)
MALFORMED = (
    "Your last message was a malformed tool call. Call the tool again through the tool-calling "
    "interface, or give your final answer."
)
HARMONY_MARKERS = ("<|call|>", "<|channel|>", "<|start|>")
CONTEXT_SUFFIX = "@ctx"
EVICTED = "[evicted to fit the context window]"
BUDGET_WARNING = (
    "You have {n} tool calls left. Make sure your changes are written before they run out."
)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})

    def openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Harness(Protocol):
    name: str

    def setup(self, kb_dir: Path, scratch: Path) -> list[ToolSpec]: ...

    def call(self, name: str, args: dict[str, Any]) -> str: ...

    def teardown(self) -> None: ...


def truncate(text: str, limit: int = MAX_RESULT_TOKENS) -> str:
    toks = llm.ENC.encode(text, disallowed_special=())
    if len(toks) <= limit:
        return text
    return llm.ENC.decode(toks[:limit]) + f"\n[... truncated: {len(toks) - limit} more tokens]"


def _approx_prompt_tokens(messages: list[dict], tools: list[dict] | None) -> int:
    return llm.ntok(json.dumps(messages, ensure_ascii=False)) + (
        llm.ntok(json.dumps(tools)) if tools else 0
    )


ChatFn = Callable[..., dict[str, Any]]


def parse_model(model: str) -> tuple[str, int | None]:
    """`gpt-oss:120b@ctx8k` -> ("gpt-oss:120b", 8192); a plain model id has no window."""
    if CONTEXT_SUFFIX not in model:
        return model, None
    api, size = model.rsplit(CONTEXT_SUFFIX, 1)
    return api, int(size.removesuffix("k")) * 1024


def fit_window(
    messages: list[dict[str, Any]], tools: list[dict] | None, budget: int, ratio: float
) -> int | None:
    """Evict (oldest first) tool results older than the newest assistant turn, then carried
    assistant content, until `ratio` x the estimated prompt fits `budget`. Messages are replaced,
    never mutated, and every tool message keeps its tool_call_id. Returns the number of messages
    evicted, or None if the prompt cannot fit."""

    def fits() -> bool:
        return ratio * _approx_prompt_tokens(messages, tools) <= budget

    last_asst = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=0)
    order = [i for i, m in enumerate(messages) if m["role"] == "tool" and i < last_asst]
    order += [i for i, m in enumerate(messages) if m["role"] == "assistant"]
    evicted = 0
    for i in order:
        if fits():
            return evicted
        if messages[i].get("content") and messages[i]["content"] != EVICTED:
            messages[i] = {**messages[i], "content": EVICTED}
            evicted += 1
    return evicted if fits() else None


def run_agent(
    model: str,
    system: str,
    user: str,
    harness: Harness | None,
    tools: list[ToolSpec],
    max_steps: int,
    chat: ChatFn = llm.chat,
    max_tokens: int = 2000,
    warn_left: int = 0,
) -> dict[str, Any]:
    """Run until the model answers without a tool call, or the step cap forces a final answer."""
    model, window = parse_model(model)
    ratio = 1.0  # gateway prompt tokens / local estimate, learned per run (never decreases)
    evictions = 0
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    otools = [t.openai() for t in tools] or None
    turns: list[dict[str, Any]] = []
    final = ""
    status = "ok"
    reasked = False
    malformed = 0
    t_start = time.perf_counter()
    step = 0
    while step <= max_steps:
        forced = step == max_steps
        if forced and not reasked:
            messages.append(
                {
                    "role": "user",
                    "content": "Tool budget exhausted. Give your final answer now, "
                    "in the required format, without calling tools.",
                }
            )
        use_tools = None if forced or reasked else otools
        evicted = 0
        if window is not None:
            fitted = fit_window(messages, use_tools, window - max_tokens, ratio)
            if fitted is None:
                status = "context_overflow"
                final = ""
                break
            evicted = fitted
            evictions += evicted
        sent_est = _approx_prompt_tokens(messages, use_tools)
        try:
            resp = chat(model, messages, tools=use_tools, max_tokens=max_tokens)
        except llm.LLMError as e:
            status = "FAILED"
            final = f"[agent failed: {e}]"
            break
        msg = resp["message"]
        usage = resp.get("usage") or {}
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        turn: dict[str, Any] = {
            "step": step,
            "prompt_tokens": pt if pt is not None else _approx_prompt_tokens(messages, use_tools),
            "completion_tokens": ct
            if ct is not None
            else llm.ntok(msg.get("content") or "")
            + llm.ntok(json.dumps(msg.get("tool_calls") or [])),
            "usage_source": "api" if pt is not None else "tiktoken",
            "latency_ms": resp.get("latency_ms"),
            "cached": resp.get("cached", False),
            "finish_reason": resp.get("finish_reason"),
            "content": msg.get("content") or "",
            "tool_calls": [],
        }
        if msg.get("reasoning"):
            turn["reasoning"] = msg["reasoning"]
        if window is not None:
            turn["evicted"] = evicted
            if pt:
                ratio = max(ratio, pt / max(sent_est, 1))
        calls = msg.get("tool_calls") or []
        if forced or reasked:
            calls = []
        content = msg.get("content") or ""
        if not calls and not forced and not reasked and any(m in content for m in HARMONY_MARKERS):
            # counts as a step (no free retries); tools stay available
            malformed += 1
            turns.append(turn)
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": MALFORMED})
            step += 1
            continue
        if not calls:
            final = msg.get("content") or ""
            turns.append(turn)
            if final.strip() or reasked:
                break
            # gpt-oss sometimes ends with an empty reply (reasoning only, or cut at max_tokens):
            # ask once more, without tools, for the final answer. Identical in every condition.
            reasked = True
            messages.append({"role": "assistant", "content": ""})
            messages.append({"role": "user", "content": EMPTY_REASK})
            continue
        carried = content or msg.get("reasoning") or ""
        messages.append({"role": "assistant", "content": carried, "tool_calls": calls})
        for c in calls:
            fn = c.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            args: Any = {"_raw": raw_args}
            try:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                if not isinstance(parsed, dict):
                    raise ValueError("arguments must be a JSON object")
                args = parsed
                if harness is None:
                    raise ValueError("no tools available")
                result = harness.call(name, args)
            except Exception as e:  # a bad call is reported to the model, never crashes the run
                result = f"ERROR: {type(e).__name__}: {e}"
            shown = truncate(result)
            turn["tool_calls"].append(
                {
                    "name": name,
                    "args": args,
                    "result_tokens": llm.ntok(result),
                    "shown_tokens": llm.ntok(shown),
                    "result": shown,
                }
            )
            messages.append({"role": "tool", "tool_call_id": c.get("id", name), "content": shown})
        turns.append(turn)
        step += 1
        if warn_left and step == max_steps - warn_left:
            # WRITE/UPDATE backstop (identical in every condition): writers looped on searches
            # until the cap without writing, so they get one warning a few steps before it.
            messages.append({"role": "user", "content": BUDGET_WARNING.format(n=warn_left)})
    return {
        "status": status,
        "final": final,
        "turns": turns,
        "steps": len(turns),
        "tool_calls": sum(len(t["tool_calls"]) for t in turns),
        "prompt_tokens": sum(t["prompt_tokens"] for t in turns),
        "completion_tokens": sum(t["completion_tokens"] for t in turns),
        "total_tokens": sum(t["prompt_tokens"] + t["completion_tokens"] for t in turns),
        "latency_ms": round(sum((t["latency_ms"] or 0) for t in turns), 1),
        "wall_s": round(time.perf_counter() - t_start, 2),
        "hit_step_cap": any(t["step"] == max_steps for t in turns),
        "reasked_empty": reasked,
        "malformed_calls": malformed,
        "tool_schema_tokens": llm.ntok(json.dumps(otools)) if otools else 0,
        **({"context_window": window, "evictions": evictions} if window is not None else {}),
    }
