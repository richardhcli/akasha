#!/usr/bin/env python3
"""
Bridge between fleet-worker agents and the Cursor Agent CLI.

Cursor is invoked as a subprocess (not via the Agent tool, which has no
native Cursor support). This script takes a task JSON on stdin, composes
a prompt, invokes cursor-agent in non-interactive mode, and returns a
result JSON on stdout describing what Cursor did and what it changed.

**Model Selection (modular for future refactoring):**
Currently defaults to Cursor Grok 4.5 High (--model grok-4.5-high).
Override via --model flag or AKASHA_FLEET_CURSOR_MODEL env var.
Other available Cursor models: composer-2.5, gpt-5.3-codex-*,
claude-opus-4-8, etc. (see `cursor-agent models` for full list).

**Abstract executor interface:** This script is the default implementation
of the "edit executor" contract. A future implementation (non-Cursor) can
replace this script as long as it implements the same JSON in/out interface.

Usage:
  echo '{"task_id":"T2.4","goal":"...","files":["..."],"constraints":"..."}' | \
    python scripts/fleet/cursor_bridge.py

Input JSON schema:
  {
    "task_id": str,           # e.g. "T2.1"
    "goal": str,              # Task goal
    "files": [str],           # Files it may touch
    "constraints": str        # Non-negotiable rules (verbatim from CLAUDE.md)
  }

Output JSON schema:
  {
    "status": "completed" | "unavailable" | "timeout" | "error",
    "files_changed": [str],   # (only if status=="completed")
    "diff_stat": str,         # git diff --stat output (only if status=="completed")
    "cursor_result_text": str,# Last line of Cursor's output (only if status=="completed")
    "usage": {                # (only if status=="completed")
      "inputTokens": int,
      "outputTokens": int,
      "cacheReadTokens": int,
      "cacheWriteTokens": int
    },
    "reason": str,            # (only if status=="unavailable")
    "detail": str             # (only if status=="error" or "timeout")
  }
"""

import json
import os
import subprocess
import sys
import argparse
import shutil
from pathlib import Path


def check_cursor_available() -> tuple[bool, str]:
    """Check if cursor-agent is on PATH and authenticated. Returns (is_available, reason)."""
    if not shutil.which("cursor-agent"):
        return False, "cursor-agent not found on PATH"

    try:
        result = subprocess.run(
            ["cursor-agent", "status"], capture_output=True, text=True, timeout=5
        )
        if "Logged in" in result.stdout:
            return True, ""
        return False, f"cursor-agent not logged in: {result.stdout.strip()}"
    except subprocess.TimeoutExpired:
        return False, "cursor-agent status check timed out"
    except Exception as e:
        return False, f"Failed to check cursor-agent status: {e}"


def compose_prompt(task_json: dict) -> str:
    """Compose a detailed prompt for Cursor from the task JSON."""
    task_id = task_json.get("task_id", "?")
    goal = task_json.get("goal", "")
    files = task_json.get("files", [])
    constraints = task_json.get("constraints", "")

    files_str = ", ".join(files) if files else "(auto-detect from context)"

    return f"""You are a code-editing agent assisting with task {task_id} in the akasha repository.

## Goal
{goal}

## Files you may touch
{files_str}

## Non-negotiable constraints
{constraints}

Proceed with the edit. Make the changes needed to accomplish the goal, respecting all constraints.
When done, provide a brief summary of what you changed."""


def run_cursor(prompt: str, model: str = "grok-4.5-high", timeout: int = 600) -> dict:
    """
    Invoke cursor-agent non-interactively with a prompt.

    Returns a dict with:
    - "status": "success" or "error"
    - "output": the result text (if success)
    - "usage": token usage dict (if success)
    - "detail": error message (if not success)
    """
    try:
        cmd = [
            "cursor-agent",
            "-p",  # Print mode (non-interactive)
            "--output-format",
            "json",
            "--model",
            model,
            "--force",  # Allow all commands without prompting
            "--trust",  # Trust this workspace
        ]

        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=Path(__file__).parent.parent.parent,  # repo root
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "detail": f"cursor-agent exited with code {result.returncode}: {result.stderr}",
            }

        # Parse the JSON output (single line expected)
        lines = result.stdout.strip().split("\n")
        if not lines:
            return {"status": "error", "detail": "No output from cursor-agent"}

        # Cursor returns JSON; take the last non-empty line in case there's logging noise
        cursor_json_line = None
        for line in reversed(lines):
            if line.strip().startswith("{"):
                cursor_json_line = line
                break

        if not cursor_json_line:
            return {
                "status": "error",
                "detail": f"Could not parse JSON from cursor-agent output:\n{result.stdout}",
            }

        cursor_result = json.loads(cursor_json_line)

        if cursor_result.get("is_error"):
            return {
                "status": "error",
                "detail": f"Cursor error: {cursor_result.get('result', 'unknown error')}",
            }

        return {
            "status": "success",
            "output": cursor_result.get("result", ""),
            "usage": cursor_result.get("usage", {}),
        }

    except subprocess.TimeoutExpired:
        return {"status": "timeout", "detail": f"Cursor timed out after {timeout}s"}
    except json.JSONDecodeError as e:
        return {"status": "error", "detail": f"Failed to parse cursor-agent JSON: {e}"}
    except Exception as e:
        return {"status": "error", "detail": f"Failed to run cursor-agent: {e}"}


def get_git_diff_stat() -> str:
    """Run 'git diff --stat' and return the output."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent.parent.parent,
        )
        return result.stdout if result.returncode == 0 else "(git diff failed)"
    except Exception:
        return "(could not compute diff)"


def get_git_changed_files() -> list[str]:
    """Run 'git diff --name-only' and return the list of changed files."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent.parent.parent,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("\n") if result.stdout.strip() else []
        return []
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(description="Bridge fleet-worker agents to Cursor Agent CLI")
    parser.add_argument(
        "--model",
        default=os.environ.get("AKASHA_FLEET_CURSOR_MODEL", "grok-4.5-high"),
        help="Cursor model to use (default: grok-4.5-high, env: AKASHA_FLEET_CURSOR_MODEL)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout for cursor-agent invocation (default: 600s)",
    )
    args = parser.parse_args()

    # Read task JSON from stdin
    try:
        task_json = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        result = {"status": "error", "detail": f"Failed to parse input JSON: {e}"}
        print(json.dumps(result))
        return 1

    # Check Cursor availability
    available, reason = check_cursor_available()
    if not available:
        result = {"status": "unavailable", "reason": reason}
        print(json.dumps(result))
        return 0  # Not an error — worker will fall back to direct edit

    # Compose prompt and run Cursor
    prompt = compose_prompt(task_json)
    cursor_result = run_cursor(prompt, model=args.model, timeout=args.timeout)

    # Build output
    if cursor_result["status"] == "success":
        files_changed = get_git_changed_files()
        diff_stat = get_git_diff_stat()
        output = {
            "status": "completed",
            "files_changed": files_changed,
            "diff_stat": diff_stat,
            "cursor_result_text": cursor_result["output"][:200],  # First 200 chars
            "usage": cursor_result.get("usage", {}),
        }
    elif cursor_result["status"] == "timeout":
        output = {"status": "timeout", "detail": cursor_result["detail"]}
    else:  # error
        output = {"status": "error", "detail": cursor_result["detail"]}

    print(json.dumps(output))
    return 0 if cursor_result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
