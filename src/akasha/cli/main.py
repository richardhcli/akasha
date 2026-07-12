"""CLI verbs (build-plan task T4.8, spec §4.12): a pure HTTP client.

This module is a thin ``typer`` client over the localhost API (spec §4.11)
— it never imports ``kernel/store.py`` and never touches SQLite directly
(build-plan rule 0.4; every persistent write happens on the daemon side,
behind the API). The one exception is ``kernel.ids.mint()`` for
client-side facet-id generation (``--facet name=span``, see below) — that
function is documented as pure/DB-free (spec §4.1, ``kernel/ids.py``
docstring: "Minting here is pure (no DB access)") and is already reused
by the (non-store) contract layer (``contract/render.py``,
``contract/linter.py``) for the same reason, so this is not a rule-0.4
violation.

Verbs (spec §4.12): ``new/get/set/rm/search/review/token/daemon``. Unlike
every other verb, ``daemon`` does not speak HTTP to an already-running
server -- it *is* the server process: it loads config, acquires the
single-instance lock (``akasha.daemon.single_instance_lock``, build-plan
task T4.9), and serves the API in-process via uvicorn. That work lives in
``akasha/daemon.py`` (not here) so this module's "pure HTTP client, no
SQLite" contract holds for every other verb; the ``daemon`` command below
is a thin dispatch to ``akasha.daemon.serve``.

Global flags: ``--json`` (versioned ``cli/v1`` output, additive-only),
``--dry-run`` (mutating verbs print the would-be request and exit 0
without sending it), ``--token`` (bearer). ``--base-url`` is this client's
own (spec-silent) wiring detail for pointing at a non-default daemon —
not one of the spec's named verbs/flags, needed only so the CLI can be
pointed at a test daemon; defaults to the spec's ``127.0.0.1:7433``.

Exit codes (spec §4.12): 0 ok · 1 error · 2 usage · 3 not found · 4
conflict/violation/needs-redirect. Click/typer already exits 2 on its own
argument-parsing failures (missing/malformed CLI args), so this module
only needs to map *server* responses (via ``_exit_code_for``) plus a
handful of client-side "usage" checks (e.g. a malformed ``--facet``
value) that typer's own parser cannot validate.

SPEC-QUESTION (T4.8): ``review list``/``review resolve`` call
``GET /v1/review`` / ``POST /v1/review/{id}/resolve`` exactly as spec'd,
but those routes do not exist yet (M7/T7.5) — the daemon currently 404s
with FastAPI's own (non-envelope) 404 body, not the spec §4.11
``{"error": {...}}`` shape. Narrowest reading taken: treat any
HTTP 404 (envelope or not) as "not found" -> exit 3, via the same
``_parse_error_body`` fallback used for any non-JSON error body. This
verb is implemented against the *documented* endpoint shape and will
start round-tripping for real the moment T7.5 lands the routes; no
CLI-side change should be needed then. Logged in docs/spec-questions.md.
"""

from __future__ import annotations

import json as json_lib
from dataclasses import dataclass
from enum import Enum
from typing import Any, NoReturn

import httpx
import typer

from akasha import daemon as daemon_module
from akasha.config import DEFAULT_BIND, DEFAULT_PORT, load_config
from akasha.kernel import ids

DEFAULT_BASE_URL = f"http://{DEFAULT_BIND}:{DEFAULT_PORT}"
CLI_SCHEMA = "cli/v1"

app = typer.Typer(add_completion=False, no_args_is_help=True)
review_app = typer.Typer(add_completion=False, no_args_is_help=True)
token_app = typer.Typer(add_completion=False, no_args_is_help=True)
app.add_typer(review_app, name="review")
app.add_typer(token_app, name="token")


class ChangeClass(str, Enum):
    patch = "patch"
    minor = "minor"
    major = "major"


@dataclass
class CliState:
    base_url: str
    token: str | None
    json_mode: bool
    dry_run: bool


def _state(ctx: typer.Context) -> CliState:
    assert isinstance(ctx.obj, CliState)  # noqa: S101 - internal invariant, not user input
    return ctx.obj


@app.callback()
def main(
    ctx: typer.Context,
    base_url: str = typer.Option(
        DEFAULT_BASE_URL, "--base-url", help="daemon base URL (default: spec §3 127.0.0.1:7433)"
    ),
    token: str | None = typer.Option(None, "--token", help="bearer token"),
    as_json: bool = typer.Option(
        False, "--json", help="emit versioned cli/v1 JSON instead of a plain body dump"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="mutating verbs print the would-be request and exit 0 without sending it",
    ),
) -> None:
    ctx.obj = CliState(
        base_url=base_url.rstrip("/"), token=token, json_mode=as_json, dry_run=dry_run
    )


# --- output / error plumbing -----------------------------------------------


def _echo_ok(state: CliState, data: Any) -> None:
    if state.json_mode:
        typer.echo(json_lib.dumps({"schema": CLI_SCHEMA, "ok": True, "data": data}))
    else:
        typer.echo(json_lib.dumps(data, indent=2, sort_keys=True))


def _echo_dry_run(state: CliState, method: str, path: str, body: dict[str, Any] | None) -> None:
    if state.json_mode:
        typer.echo(
            json_lib.dumps(
                {
                    "schema": CLI_SCHEMA,
                    "ok": True,
                    "dry_run": True,
                    "request": {"method": method, "path": path, "body": body},
                }
            )
        )
    else:
        typer.echo(
            json_lib.dumps(
                {"dry_run": True, "method": method, "path": path, "body": body},
                indent=2,
                sort_keys=True,
            )
        )


def _exit_code_for(status_code: int, code: str) -> int:
    """Map an HTTP status / error code to a spec §4.12 exit code.

    404 / ``E_NOT_FOUND`` -> 3; 409 / ``E_NEEDS_REDIRECT`` (and any other
    conflict-ish code) -> 4; everything else the server returns -> 1
    (usage errors, code 2, are reserved for this CLI's own argument
    parsing — see module docstring).
    """
    if status_code == 404 or code == "E_NOT_FOUND":
        return 3
    conflict_codes = {"E_NEEDS_REDIRECT"}
    if status_code == 409 or code in conflict_codes or "CONFLICT" in code or "VIOLATION" in code:
        return 4
    return 1


def _parse_error_body(resp: httpx.Response) -> tuple[str, str, dict[str, Any]]:
    try:
        payload = resp.json()
        err = payload["error"]
        return (
            str(err.get("code", "E_UNKNOWN")),
            str(err.get("message", resp.text)),
            dict(err.get("detail", {})),
        )
    except Exception:
        # Non-envelope body (e.g. FastAPI's own 404 for an unregistered
        # route, spec §4.12 review-endpoint SPEC-QUESTION above).
        return "E_UNKNOWN", (resp.text or f"HTTP {resp.status_code}"), {}


def _fail(
    state: CliState, status_code: int, code: str, message: str, detail: dict[str, Any]
) -> NoReturn:
    exit_code = _exit_code_for(status_code, code)
    if state.json_mode:
        typer.echo(
            json_lib.dumps(
                {
                    "schema": CLI_SCHEMA,
                    "ok": False,
                    "error": {"code": code, "message": message, "detail": detail},
                }
            ),
            err=True,
        )
    else:
        typer.echo(f"error: {code}: {message}", err=True)
    raise typer.Exit(exit_code)


def _usage_error(message: str) -> NoReturn:
    typer.echo(f"usage error: {message}", err=True)
    raise typer.Exit(2)


def _request(
    state: CliState,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    headers = {"Authorization": f"Bearer {state.token}"} if state.token else {}
    try:
        resp = httpx.request(
            method,
            f"{state.base_url}{path}",
            params=params,
            json=json_body,
            headers=headers,
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        _fail(state, 0, "E_CONNECTION", str(exc), {})
    if resp.status_code >= 400:
        code, message, detail = _parse_error_body(resp)
        _fail(state, resp.status_code, code, message, detail)
    if not resp.content:
        return {}
    return resp.json()


def _mutate(
    state: CliState, method: str, path: str, json_body: dict[str, Any] | None = None
) -> Any:
    if state.dry_run:
        _echo_dry_run(state, method, path, json_body)
        raise typer.Exit(0)
    return _request(state, method, path, json_body=json_body)


def _parse_facets(raw: list[str]) -> list[dict[str, Any]]:
    """Parse repeated ``--facet name=span`` into full ``Facet`` dicts.

    The API's ``Facet`` model (spec §4.2) requires ``facet_id``/``version``
    in addition to ``name``/``span``; the CLI syntax only names the two
    human-supplied fields (spec §4.12), so a fresh ``facet_id`` is minted
    client-side (``kernel.ids.mint()`` — pure, no DB, see module
    docstring) and ``version`` starts at 1, matching a brand-new facet.
    """
    facets: list[dict[str, Any]] = []
    for item in raw:
        if "=" not in item:
            _usage_error(f"--facet must be name=span, got {item!r}")
        name, span = item.split("=", 1)
        if not name:
            _usage_error(f"--facet name must be non-empty, got {item!r}")
        facets.append({"facet_id": ids.mint(), "name": name, "span": span, "version": 1})
    return facets


# --- daemon --------------------------------------------------------------


@app.command()
def daemon(
    config: str | None = typer.Option(
        None, "--config", help="path to config.toml (default: per-OS default location)"
    ),
) -> None:
    """Run the akasha daemon (spec §4.12): serve the API until shutdown.

    Acquires a single-instance lock before binding ``config.bind``/
    ``config.port`` (default ``127.0.0.1:7433``); a second concurrent
    instance exits cleanly with a human-readable message (no traceback)
    rather than starting a competing server. Exit code 4 -- the spec
    §4.12 "conflict" class -- since a second instance is a conflict over
    the single-instance lock resource, not a generic error (1) or usage
    mistake (2).

    Note: this verb does not go through ``--base-url``/``--token``/
    ``--json``/``--dry-run`` -- those are for the HTTP-client verbs above;
    ``daemon`` is a foreground process command.
    """
    cfg = load_config(config)
    try:
        daemon_module.serve(cfg)
    except daemon_module.AlreadyRunningError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(4) from exc


# --- new/get/set/rm/search ---------------------------------------------------


@app.command()
def new(
    ctx: typer.Context,
    node_type: str = typer.Argument(
        ..., help="entity|definition|claim|relation|proof|evidence|task"
    ),
    body: str = typer.Argument(..., help="node body text"),
    facet: list[str] = typer.Option([], "--facet", help="name=span, repeatable"),
    task: bool = typer.Option(False, "--task", help="create as an open task"),
) -> None:
    """POST /v1/nodes."""
    state = _state(ctx)
    facets = _parse_facets(facet)
    payload: dict[str, Any] = {"node_type": node_type, "body": body}
    if facets:
        payload["facets"] = facets
    if task:
        payload["task_state"] = "open"
    result = _mutate(state, "POST", "/v1/nodes", payload)
    _echo_ok(state, result)


@app.command()
def get(
    ctx: typer.Context,
    node_id: str,
    as_of: str | None = typer.Option(None, "--as-of", help="ISO timestamp"),
) -> None:
    """GET /v1/nodes/{id} (+?as_of=)."""
    state = _state(ctx)
    params = {"as_of": as_of} if as_of else None
    result = _request(state, "GET", f"/v1/nodes/{node_id}", params=params)
    _echo_ok(state, result)


@app.command(name="set")
def set_(
    ctx: typer.Context,
    node_id: str,
    body: str | None = typer.Option(None, "--body"),
    change_class: ChangeClass = typer.Option(
        ChangeClass.patch, "--class", help="patch|minor|major (default: patch)"
    ),
    touch: list[str] = typer.Option([], "--touch", help="facet name, repeatable"),
) -> None:
    """PATCH /v1/nodes/{id}."""
    state = _state(ctx)
    payload: dict[str, Any] = {
        "body": body,
        "change_class": change_class.value,
        "facets_touched": touch,
    }
    result = _mutate(state, "PATCH", f"/v1/nodes/{node_id}", payload)
    _echo_ok(state, result)


@app.command()
def rm(
    ctx: typer.Context,
    node_id: str,
    redirect_to: list[str] = typer.Option([], "--redirect-to", help="successor id, repeatable"),
) -> None:
    """DELETE /v1/nodes/{id}; S1+ needs --redirect-to (409 E_NEEDS_REDIRECT -> exit 4)."""
    state = _state(ctx)
    payload: dict[str, Any] | None = {"redirect_to": redirect_to} if redirect_to else None
    result = _mutate(state, "DELETE", f"/v1/nodes/{node_id}", payload)
    _echo_ok(state, result)


@app.command()
def search(ctx: typer.Context, q: str) -> None:
    """GET /v1/search?q=."""
    state = _state(ctx)
    result = _request(state, "GET", "/v1/search", params={"q": q})
    _echo_ok(state, result)


# --- review ------------------------------------------------------------------


@review_app.command("list")
def review_list(
    ctx: typer.Context, status: str = typer.Option("open", "--status")
) -> None:
    """GET /v1/review?status=."""
    state = _state(ctx)
    result = _request(state, "GET", "/v1/review", params={"status": status})
    _echo_ok(state, result)


@review_app.command("resolve")
def review_resolve(ctx: typer.Context, review_id: str, resolution: str) -> None:
    """POST /v1/review/{id}/resolve (human only ∅)."""
    state = _state(ctx)
    result = _mutate(state, "POST", f"/v1/review/{review_id}/resolve", {"resolution": resolution})
    _echo_ok(state, result)


# --- token ---------------------------------------------------------------


@token_app.command("create")
def token_create(
    ctx: typer.Context,
    name: str,
    token_class: str = typer.Option("agent", "--class", help="human|agent"),
    rate_per_min: int | None = typer.Option(None, "--rate-per-min"),
) -> None:
    """POST /v1/tokens (human only ∅)."""
    state = _state(ctx)
    if token_class not in ("human", "agent"):
        _usage_error(f"--class must be 'human' or 'agent', got {token_class!r}")
    payload: dict[str, Any] = {"name": name, "token_class": token_class}
    if rate_per_min is not None:
        payload["rate_per_min"] = rate_per_min
    result = _mutate(state, "POST", "/v1/tokens", payload)
    _echo_ok(state, result)


@token_app.command("revoke")
def token_revoke(ctx: typer.Context, token_id: str) -> None:
    """DELETE /v1/tokens/{id} (human only ∅)."""
    state = _state(ctx)
    result = _mutate(state, "DELETE", f"/v1/tokens/{token_id}")
    _echo_ok(state, result)


@token_app.command("list")
def token_list(ctx: typer.Context) -> None:
    """GET /v1/tokens (human only ∅)."""
    state = _state(ctx)
    result = _request(state, "GET", "/v1/tokens")
    _echo_ok(state, result)


if __name__ == "__main__":
    app()
