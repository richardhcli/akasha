"""`python -m kbio.probe <model>`: wait until the endpoint answers a tiny request for <model>.

Used in a job chain (`probe && kbio run ...`) when a model hangs on the endpoint: one uncached
request every INTERVAL s (through the shared throttle), exit 0 on the first answer, 1 after
MAX_HOURS. Each attempt is logged to llm-calls.log like any other request.

`python -m kbio.probe <model> --fields`: one tool-call request; prints which fields the endpoint's
message carries (does it return reasoning that the agent loop would drop?)."""

from __future__ import annotations

import os
import sys
import time

import requests

from kbio import llm
from kbio.env import load_purdue_key

INTERVAL = 120
TIMEOUT = 60
MAX_HOURS = 6.0


def healthy(model: str) -> bool:
    load_purdue_key()
    headers = {"Authorization": f"Bearer {os.environ['PURDUE_GENAI_API_KEY']}"}
    body = {"model": model, "messages": [{"role": "user", "content": "Say OK."}],
            "max_tokens": 20, "stream": False}  # fmt: skip
    llm._wait_turn()
    try:
        r = requests.post(llm.URL, headers=headers, json=body, timeout=TIMEOUT)
        outcome = f"probe-http-{r.status_code}"
        ok = r.status_code == 200
    except requests.RequestException as e:
        outcome, ok = f"probe-exc-{type(e).__name__}", False
    llm._log_call(model, outcome)
    return ok


def fields(model: str) -> None:
    load_purdue_key()
    headers = {"Authorization": f"Bearer {os.environ['PURDUE_GENAI_API_KEY']}"}
    tool = {"type": "function", "function": {"name": "search", "description": "Search notes.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                           "required": ["query"]}}}  # fmt: skip
    body = {"model": model, "stream": False, "temperature": 0, "max_tokens": 800, "tools": [tool],
            "messages": [{"role": "user", "content": "What do my notes say about habits? "
                          "Use the search tool."}]}  # fmt: skip
    llm._wait_turn()
    r = requests.post(llm.URL, headers=headers, json=body, timeout=150)
    llm._log_call(model, f"probe-fields-http-{r.status_code}")
    data = r.json()
    choice = data["choices"][0]
    print("choice keys:", sorted(choice))
    for k, v in choice["message"].items():
        print(f"message.{k}: {str(v)[:300]!r}")


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "gpt-oss:120b"
    if "--fields" in sys.argv:
        fields(model)
        return
    t0 = time.time()
    while time.time() - t0 < MAX_HOURS * 3600:
        if healthy(model):
            print(f"{model} healthy after {round(time.time() - t0)} s", flush=True)
            return
        print(f"{time.strftime('%H:%M:%S')} {model} not answering", flush=True)
        time.sleep(INTERVAL)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
