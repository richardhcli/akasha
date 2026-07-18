"""Dedicated ``--dry-run`` coverage enumeration (task T9.4, spec §4.12).

``tests/integration/test_cli.py`` (task T4.8) already exercises
``--dry-run`` for a few individual verbs, but per-verb, ad hoc. This file
is the *systematic* complement: it enumerates every verb the live
``typer`` app actually registers (top-level commands plus the ``review``/
``token`` sub-typers) and asserts, table-driven, that:

1. Every verb classified as "mutating" (source-scanned: it issues a
   POST/PATCH/PUT/DELETE) returns the would-be ``{method, path, body}``
   request under ``--dry-run`` and *never* touches the network (proven by
   pointing ``--base-url`` at an address nothing listens on — a
   ``ConnectionError`` would surface as a non-zero exit / real exception
   if the dry-run short-circuit were missing or bypassed, same technique
   as T4.8's own ``test_rm_dry_run_mutates_nothing_and_never_hits_network``).
2. No mutating verb reaches the network by calling ``_request`` directly
   with a mutating HTTP method instead of the shared ``_mutate`` helper —
   a static source check, so a *future* verb that adds a new POST/PATCH/
   DELETE call without wiring it through ``_mutate`` fails this test
   immediately, before it ever ships without ``--dry-run`` coverage.
3. The discovered set of mutating verbs exactly matches the hand-maintained
   ``DRY_RUN_CASES`` table below — if a new mutating verb is added to
   ``cli/main.py``, this test fails until a corresponding case is added
   here (closing the "future verb slips through uncovered" gap).

Read-only verbs (``get``, ``search``, ``review list``, ``token list``)
and ``daemon`` (a foreground process command, not an HTTP verb — see
``cli/main.py`` module docstring) are deliberately excluded from
``DRY_RUN_CASES``; the source scan (point 2 above) is what guards against
them silently growing a mutating code path.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest
from typer.testing import CliRunner

from akasha.cli.main import app as cli_app

runner = CliRunner()

# Nothing listens here; any real HTTP attempt raises a connection error
# instead of a clean dry-run exit, same precedent as T4.8's own dry-run
# tests in tests/integration/test_cli.py.
UNREACHABLE_BASE_URL = "http://127.0.0.1:1"

MUTATING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}

_CALL_RE = re.compile(r"_(request|mutate)\(\s*state,\s*\"([A-Z]+)\"")


def _iter_registered_commands() -> list[tuple[str, Callable[..., Any]]]:
    """Every command the live app registers: ``(dotted name, callback)``.

    Walks ``app.registered_commands`` (top-level) plus one level of
    ``app.registered_groups`` (``review``, ``token``) — the exact shape
    ``cli/main.py`` uses today (spec §4.12 lists no deeper nesting).
    """
    commands: list[tuple[str, Callable[..., Any]]] = []
    for info in cli_app.registered_commands:
        assert info.callback is not None
        name = info.name or info.callback.__name__
        commands.append((name, info.callback))
    for group in cli_app.registered_groups:
        assert group.typer_instance is not None
        sub_app = group.typer_instance
        for info in sub_app.registered_commands:
            assert info.callback is not None
            name = info.name or info.callback.__name__
            commands.append((f"{group.name} {name}", info.callback))
    return commands


def _mutating_calls(callback: Callable[..., Any]) -> list[tuple[str, str]]:
    """``[(helper, HTTP method), ...]`` for every ``_request``/``_mutate``
    call with a mutating method found in ``callback``'s source."""
    source = inspect.getsource(callback)
    found = _CALL_RE.findall(source)
    return [(helper, method) for helper, method in found if method in MUTATING_METHODS]


def test_no_mutating_verb_bypasses_dry_run_via_raw_request() -> None:
    """Static guard: a mutating HTTP call must go through ``_mutate``, not
    ``_request`` directly -- ``_request`` has no dry-run short-circuit."""
    offenders = []
    for name, callback in _iter_registered_commands():
        for helper, method in _mutating_calls(callback):
            if helper == "request":
                offenders.append(f"{name} issues {method} via _request (bypasses --dry-run)")
    assert offenders == [], "\n".join(offenders)


def _discovered_mutating_verbs() -> set[str]:
    discovered = set()
    for name, callback in _iter_registered_commands():
        if any(helper == "mutate" for helper, _method in _mutating_calls(callback)):
            discovered.add(name)
    return discovered


@dataclass(frozen=True)
class DryRunCase:
    id: str
    argv: list[str]
    method: str
    path: str
    body_check: Callable[[Any], None] = field(default=lambda body: None)


def _assert_body_has(*keys: str) -> Callable[[Any], None]:
    def _check(body: Any) -> None:
        assert isinstance(body, dict), body
        for key in keys:
            assert key in body, f"expected {key!r} in dry-run body {body!r}"

    return _check


def _assert_body_is_none(body: Any) -> None:
    assert body is None, body


DRY_RUN_CASES: list[DryRunCase] = [
    DryRunCase(
        id="new",
        argv=["new", "claim", "dry run body"],
        method="POST",
        path="/v1/nodes",
        body_check=_assert_body_has("node_type", "body"),
    ),
    DryRunCase(
        id="set",
        argv=["set", "dummynode1", "--body", "updated body"],
        method="PATCH",
        path="/v1/nodes/dummynode1",
        body_check=_assert_body_has("body", "change_class", "facets_touched"),
    ),
    DryRunCase(
        id="rm",
        argv=["rm", "dummynode1"],
        method="DELETE",
        path="/v1/nodes/dummynode1",
        body_check=_assert_body_is_none,
    ),
    DryRunCase(
        id="rm_with_redirect",
        argv=["rm", "dummynode1", "--redirect-to", "dummynode2"],
        method="DELETE",
        path="/v1/nodes/dummynode1",
        body_check=_assert_body_has("redirect_to"),
    ),
    DryRunCase(
        id="review resolve",
        argv=["review", "resolve", "dummyreview1", "accepted"],
        method="POST",
        path="/v1/review/dummyreview1/resolve",
        body_check=_assert_body_has("resolution"),
    ),
    DryRunCase(
        id="token create",
        argv=["token", "create", "ci-bot"],
        method="POST",
        path="/v1/tokens",
        body_check=_assert_body_has("name", "token_class"),
    ),
    DryRunCase(
        id="token revoke",
        argv=["token", "revoke", "dummytoken1"],
        method="DELETE",
        path="/v1/tokens/dummytoken1",
        body_check=_assert_body_is_none,
    ),
]


def test_dry_run_case_table_matches_discovered_mutating_verbs() -> None:
    """If a new mutating verb lands in ``cli/main.py`` without a matching
    entry above, this fails -- closing the "future verb slips through
    uncovered" gap the task calls out explicitly."""
    # "rm_with_redirect" is a --redirect-to variant of the "rm" verb, not a
    # distinct command the app registers.
    expected = {case.id for case in DRY_RUN_CASES} - {"rm_with_redirect"}
    assert _discovered_mutating_verbs() == expected


@pytest.mark.parametrize("case", DRY_RUN_CASES, ids=lambda c: c.id)
def test_dry_run_returns_request_and_touches_no_network(case: DryRunCase) -> None:
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            UNREACHABLE_BASE_URL,
            "--token",
            "sometoken",
            "--dry-run",
            *case.argv,
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)

    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["method"] == case.method
    assert payload["path"] == case.path
    case.body_check(payload["body"])


@pytest.mark.parametrize("case", DRY_RUN_CASES, ids=lambda c: c.id)
def test_dry_run_json_flag_emits_cli_v1_envelope(case: DryRunCase) -> None:
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            UNREACHABLE_BASE_URL,
            "--token",
            "sometoken",
            "--json",
            "--dry-run",
            *case.argv,
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["request"]["method"] == case.method
    assert payload["request"]["path"] == case.path
    case.body_check(payload["request"]["body"])


def test_dry_run_flag_order_does_not_matter() -> None:
    """``--dry-run`` is a global flag on the callback -- must work whether
    it precedes or follows other global flags, not just verb args."""
    result = runner.invoke(
        cli_app,
        [
            "--dry-run",
            "--base-url",
            UNREACHABLE_BASE_URL,
            "--token",
            "sometoken",
            "rm",
            "dummynode1",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["method"] == "DELETE"


# --- error-message audit: --usage-error now honors --json (T9.4 fix) -------


def test_usage_error_respects_json_flag() -> None:
    """Regression test for the T9.4 audit finding: ``_usage_error`` (client
    -side validation, exit 2) previously always printed plain text, even
    under ``--json`` -- inconsistent with ``_fail`` (server errors), which
    always emitted the ``cli/v1`` envelope. Both must now agree."""
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            UNREACHABLE_BASE_URL,
            "--token",
            "sometoken",
            "--json",
            "new",
            "claim",
            "body",
            "--facet",
            "no-equals-sign",
        ],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_USAGE"
    assert "--facet" in payload["error"]["message"]


def test_usage_error_plain_text_without_json_flag() -> None:
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            UNREACHABLE_BASE_URL,
            "--token",
            "sometoken",
            "token",
            "create",
            "x",
            "--class",
            "not-a-class",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "usage error:" in result.output
    # Plain mode must stay plain text, not accidentally emit JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)
