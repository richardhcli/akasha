"""Purdue GenAI Studio chat client with native tool calls, a cross-process throttle, retries and a
response cache. Throttle and backoff follow concepts-retrieval/scripts/run_llm.py
(MIN_INTERVAL=4.0 s between request starts, 7 retries, 60*(n+1) s on rate limits); the throttle is
a file lock so parallel tmux jobs share the endpoint's budget. Every response is cached by the
exact request, so a rerun (or a resumed agent loop) replays without calling the endpoint."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from typing import Any

import requests
import tiktoken

from kbio.env import load_purdue_key
from kbio.paths import DATA

URL = "https://genai.rcac.purdue.edu/api/chat/completions"
MIN_INTERVAL = 4.0
CACHE = DATA / "cache" / "llm"
THROTTLE = DATA / "cache" / "throttle"
ENC = tiktoken.get_encoding("cl100k_base")
STATS = {"calls": 0, "cache_hits": 0, "retries": 0}


class LLMError(RuntimeError):
    pass


def ntok(s: str) -> int:
    return len(ENC.encode(s, disallowed_special=()))


def _wait_turn() -> None:
    """Cross-process: at most one request start per MIN_INTERVAL seconds."""
    THROTTLE.parent.mkdir(parents=True, exist_ok=True)
    with open(THROTTLE, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read().strip()
        last = float(raw) if raw else 0.0
        delay = last + MIN_INTERVAL - time.time()
        if delay > 0:
            time.sleep(delay)
        f.seek(0)
        f.truncate()
        f.write(f"{time.time():.3f}")
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)


CALL_LOG = DATA / "logs" / "llm-calls.log"


def _log_call(model: str, outcome: str) -> None:
    """One line per real request (start time, model, outcome): calls/hour is measured from it."""
    CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(CALL_LOG, "a") as f:
        f.write(f"{time.time():.1f}\t{model}\t{outcome}\n")


def cache_key(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def chat(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 1500,
    temperature: float = 0,
    timeout: int = 150,
) -> dict[str, Any]:
    """One chat completion. Returns {message, usage, latency_ms, model, cached}."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
    # Tool-bearing (agent) requests since loop v2 keep the model's reasoning, which v1 cache
    # entries lack; a namespace keeps v1 entries from replaying. Judge/drafting keys are unchanged.
    key = cache_key({**body, "_kbio_loop": 2} if tools else body)
    path = CACHE / key[:2] / f"{key}.json"
    if path.exists():
        STATS["cache_hits"] += 1
        return {**json.loads(path.read_text()), "cached": True}
    load_purdue_key()
    headers = {
        "Authorization": f"Bearer {os.environ['PURDUE_GENAI_API_KEY']}",
        "Content-Type": "application/json",
    }
    last = ""
    for attempt in range(7):
        _wait_turn()
        t0 = time.perf_counter()
        try:
            r = requests.post(URL, headers=headers, json=body, timeout=timeout)
            latency = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                data = r.json()
                msg = data["choices"][0]["message"]
                usage = data.get("usage") or {}
                reasoning = msg.get("reasoning_content") or (
                    msg.get("provider_specific_fields") or {}
                ).get("reasoning")
                out = {
                    "message": {
                        "role": "assistant",
                        "content": msg.get("content") or "",
                        **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {}),
                        **({"reasoning": reasoning} if reasoning else {}),
                    },
                    "finish_reason": data["choices"][0].get("finish_reason"),
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                    },
                    "latency_ms": round(latency, 1),
                    "model": data.get("model") or model,
                }
                STATS["calls"] += 1
                _log_call(model, "ok")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(out, ensure_ascii=False) + "\n")
                return {**out, "cached": False}
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            _log_call(model, f"http-{r.status_code}")
            if 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                raise LLMError(f"{model} rejected the request: {last}")  # retrying cannot help
        except (requests.RequestException, ValueError, KeyError) as e:
            last = f"{type(e).__name__}: {str(e)[:300]}"
            _log_call(model, f"exc-{type(e).__name__}")
        STATS["retries"] += 1
        rate = "429" in last or "rate limit" in last.lower()
        time.sleep(60 * (attempt + 1) if rate else 5 * 2**attempt)
    raise LLMError(f"{model} failed after retries: {last}")


def ask(model: str, system: str, prompt: str, max_tokens: int = 800) -> str:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    return chat(model, msgs, max_tokens=max_tokens)["message"]["content"]


def extract_json(text: str) -> dict[str, Any]:
    candidates = [text]
    if "```" in text:
        candidates += [p.strip().removeprefix("json").strip() for p in text.split("```")[1::2]]
    for c in candidates:
        try:
            start, end = c.index("{"), c.rindex("}") + 1
            value = json.loads(c[start:end])
            if isinstance(value, dict):
                return value
        except (ValueError, json.JSONDecodeError):
            continue
    raise ValueError("no JSON object in model response")
