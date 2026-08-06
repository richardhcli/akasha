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

Verbs (spec §4.12): ``new/get/set/rm/search/review/token/export/daemon/init/sync``,
plus build-plan additions ``neighborhood``/``history`` (T14.1) and
``edge add``/``edge rm`` (T14.2, both over the already-shipped
``POST /v1/edges``/``DELETE /v1/edges/{id}``, spec §4.11).
Unlike every other verb, ``daemon`` does not speak HTTP to an
already-running server -- it *is* the server process: it loads config,
acquires the single-instance lock (``akasha.daemon.single_instance_lock``,
build-plan task T4.9), and serves the API in-process via uvicorn. That
work lives in ``akasha/daemon.py`` (not here) so this module's "pure HTTP
client, no SQLite" contract holds for every other verb (including
``export``, task T10.2, a pure client of ``GET /v1/sync/export`` -- see
its own docstring below); the ``daemon`` command below is a thin dispatch
to ``akasha.daemon.serve``.

``init`` (task T12.1, closing ``docs/spec-questions.md`` T11.1) is the
second, deliberate exception to the "pure HTTP client" rule: it talks to
``kernel/store.py`` directly (via ``store.connect``/``store.run_migrations``/
``store.create_token``, the same helpers ``daemon``'s startup path and
``api/routes/tokens.py::create_token`` already use) rather than a new HTTP
endpoint, because the very first human token cannot be minted through
``POST /v1/tokens`` -- that route is ``require_human`` and a fresh DB has
no token to authenticate with yet. No new authless HTTP surface is added;
``init`` mints the identical ``tokens`` row/bearer-token shape
``POST /v1/tokens`` does, via the same ``api/auth.py::mint_secret``/
``hash_secret``/``format_bearer_token`` helpers.

Global flags: ``--json`` (versioned ``cli/v1`` output, additive-only),
``--dry-run`` (mutating verbs print the would-be request and exit 0
without sending it), ``--token`` (bearer). ``--base-url`` is this client's
documented wiring override for pointing at a non-default daemon; it also
supports live integration tests and defaults to the spec's
``127.0.0.1:7433``.

Exit codes (spec §4.12): 0 ok · 1 error · 2 usage · 3 not found · 4
conflict/violation/needs-redirect. Click/typer already exits 2 on its own
argument-parsing failures (missing/malformed CLI args), so this module
only needs to map *server* responses (via ``_exit_code_for``) plus a
handful of client-side "usage" checks (e.g. a malformed ``--facet``
value) that typer's own parser cannot validate.

``review list``/``review resolve`` call the documented future
``GET /v1/review`` / ``POST /v1/review/{id}/resolve`` endpoints. Until
T7.5 lands them, any HTTP 404 (envelope or not) maps to exit 3 without a
traceback; no CLI-side contract change is expected when the routes arrive.

T9.4 audit note: every mutating verb (``new``/``set``/``rm``/
``review resolve``/``token create``/``token revoke``) already funneled
through the shared ``_mutate`` helper as of T4.8, so ``--dry-run``
coverage was already structurally complete — confirmed, not re-derived,
by ``tests/integration/test_cli_dry_run.py``'s source-scanning meta-test,
which fails if a future verb calls ``_request`` with a mutating HTTP
method (bypassing ``--dry-run``) instead of ``_mutate``. The one real gap
found and fixed: ``_usage_error`` (client-side argument validation, exit
2) did not honor ``--json`` and always printed the plain-text form even
under ``--json`` — unlike ``_fail`` (server-reported errors), which
already emitted the ``cli/v1`` envelope. Fixed by threading ``state``
through ``_usage_error`` and its callers (``_parse_facets``,
``token create``) so both client- and server-rejected requests get a
consistent, machine-parseable error shape under ``--json``. Also
clarified the connection-error message (``E_CONNECTION``) to name the
unreachable ``--base-url`` explicitly rather than a bare httpx exception
string.
"""

from __future__ import annotations

import io
import json as json_lib
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, NoReturn

import httpx
import typer

from akasha import daemon as daemon_module
from akasha.api import auth
from akasha.config import DEFAULT_BIND, DEFAULT_PORT, default_db_path, load_config
from akasha.kernel import ids, store

# Windows consoles default `sys.stdout`/`sys.stderr` to the legacy locale
# codepage (e.g. cp1252), not UTF-8 -- confirmed live on a real Windows 11
# host, where this crashed several `--help` invocations with
# UnicodeEncodeError on a plain U+2205 character in a command docstring.
# UTF-8 can represent every Unicode string losslessly, so reconfiguring
# here removes the crash risk entirely rather than avoiding specific
# characters case by case. The `isinstance` check (not just `hasattr`)
# both narrows the type for pyright and skips streams that have already
# been replaced with something that doesn't support `.reconfigure` (e.g.
# click's test `CliRunner`).
if sys.platform == "win32":  # pragma: no cover - platform-specific, see T9.1/T9.2 precedent
    for _stream in (sys.stdout, sys.stderr):
        if isinstance(_stream, io.TextIOWrapper):
            _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_BASE_URL = f"http://{DEFAULT_BIND}:{DEFAULT_PORT}"
CLI_SCHEMA = "cli/v1"

app = typer.Typer(add_completion=False, no_args_is_help=True)
review_app = typer.Typer(add_completion=False, no_args_is_help=True)
token_app = typer.Typer(add_completion=False, no_args_is_help=True)
sync_app = typer.Typer(add_completion=False, no_args_is_help=True)
edge_app = typer.Typer(add_completion=False, no_args_is_help=True)
app.add_typer(review_app, name="review")
app.add_typer(token_app, name="token")
app.add_typer(sync_app, name="sync")
app.add_typer(edge_app, name="edge")


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

    # SPEC-QUESTION (T14.2): ``POST /v1/edges``' facet-binding-rule
    # rejection (a justification edge with no ``facet_binding``) is a
    # ``400 E_INVALID`` (``api/routes/edges.py``), which this mapping
    # sends to exit 1 — spec §4.12's exit-code table reads "4
    # conflict/violation/needs-redirect", and every existing use of the
    # word "violation" elsewhere in this codebase (``cause_kind="violation"``
    # review items, ``sync/reconcile.py``) names a *contract*-violation
    # concept unrelated to generic request validation, and ``E_INVALID``
    # is used identically (400, exit 1) by every other verb's own
    # server-side validation (e.g. ``new`` with a malformed ``node_type``,
    # ``sync add`` with a bad root path) with no dedicated test anywhere
    # pinning a different exit code for it. Narrowest reading: leave this
    # shared mapping untouched rather than widen it (a cross-cutting
    # change touching every verb, not scoped to edges) — see
    # docs/spec-questions.md T14.2 entry.
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


def _usage_error(state: CliState, message: str) -> NoReturn:
    """Client-side argument-validation failure (exit 2, spec §4.12).

    ``E_USAGE`` is a CLI-local code (never sent by the server) for the
    handful of checks typer's own parser cannot express (e.g. `--facet`
    shape) — same precedent as ``_request``'s ``E_CONNECTION`` below.
    Honors ``--json`` so a scripted/machine caller always gets the
    documented ``cli/v1`` envelope regardless of which layer rejected the
    input, matching ``_fail``'s server-error behavior below.
    """
    if state.json_mode:
        typer.echo(
            json_lib.dumps(
                {
                    "schema": CLI_SCHEMA,
                    "ok": False,
                    "error": {"code": "E_USAGE", "message": message, "detail": {}},
                }
            ),
            err=True,
        )
    else:
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
        _fail(state, 0, "E_CONNECTION", f"could not reach daemon at {state.base_url}: {exc}", {})
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


def _parse_facets(state: CliState, raw: list[str]) -> list[dict[str, Any]]:
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
            _usage_error(state, f"--facet must be name=span, got {item!r}")
        name, span = item.split("=", 1)
        if not name:
            _usage_error(state, f"--facet name must be non-empty, got {item!r}")
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


@app.command()
def tray(
    config: str | None = typer.Option(
        None, "--config", help="path to config.toml (default: per-OS default location)"
    ),
) -> None:
    """Run the daemon with a system-tray icon (build-plan T12.5, optional extra).

    Same process/lock semantics as ``daemon`` (it calls the identical
    ``daemon.serve()``, just on a background thread instead of the
    foreground) -- a second concurrent instance still exits cleanly via
    ``AlreadyRunningError`` rather than opening a second icon. Requires the
    ``tray`` extra (``pystray``/``Pillow``, ``pyproject.toml``); not
    installed by default, so this prints a clear one-line install hint
    instead of a raw ``ImportError`` traceback if it's missing.

    Note: like ``daemon``/``init``, this does not go through
    ``--base-url``/``--token``/``--json``/``--dry-run``.
    """
    cfg = load_config(config)
    try:
        from akasha import tray as tray_module
    except ImportError as exc:
        typer.echo(
            "error: the tray extra is not installed -- run `uv sync --extra tray` "
            f"(or `pip install akasha[tray]`) and try again ({exc})",
            err=True,
        )
        raise typer.Exit(1) from exc
    try:
        tray_module.run(cfg)
    except daemon_module.AlreadyRunningError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(4) from exc


@app.command()
def init(
    config: str | None = typer.Option(
        None, "--config", help="path to config.toml (default: per-OS default location)"
    ),
    name: str = typer.Option("bootstrap", "--name", help="name for the minted human token"),
) -> None:
    """Bootstrap the first human token on a fresh DB (spec-questions T11.1).

    ``POST /v1/tokens`` is ``require_human``, so a brand-new database (no
    tokens at all) has no way to authenticate a call to it -- this verb
    breaks that chicken-and-egg deadlock by talking to ``kernel/store.py``
    directly instead of over HTTP (see module docstring above for why this
    is not a rule-0.4 violation). It runs migrations against ``config.db_path``
    (idempotent, safe on a genuinely fresh, schema-less DB file -- same as
    ``api/app.py``'s ``create_app`` startup path), then mints exactly one
    ``human``-class token via the identical
    ``auth.mint_secret()``/``store.create_token()``/``auth.format_bearer_token()``
    sequence ``api/routes/tokens.py::create_token`` already uses over HTTP --
    no new schema, no second write path.

    If any token already exists, this is a conflict (exit 4, spec §4.12's
    "conflict/violation" class -- same mapping ``daemon``'s
    ``AlreadyRunningError`` uses above): mints nothing, and points the
    caller at the normal ``POST /v1/tokens`` (human-only) route to mint
    further tokens once a daemon is running.

    The printed bearer token is shown exactly once, like
    ``POST /v1/tokens``'s own response -- it is never recoverable
    afterward (only its hash is persisted).

    Note: this verb does not go through ``--base-url``/``--token``/
    ``--json``/``--dry-run`` -- those are for the HTTP-client verbs above;
    like ``daemon``, ``init`` is not a pure HTTP client (see module
    docstring).
    """
    cfg = load_config(config)
    db_path = cfg.db_path if cfg.db_path is not None else default_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = store.connect(db_path, check_same_thread=False)
    store.run_migrations(conn)

    if store.list_tokens(conn):
        typer.echo(
            "error: a token already exists; use the running daemon's "
            "POST /v1/tokens (human token required) to mint another",
            err=True,
        )
        raise typer.Exit(4)

    raw_secret = auth.mint_secret()
    token = store.create_token(conn, name, "human", auth.hash_secret(raw_secret))
    bearer = auth.format_bearer_token(token["id"], raw_secret)
    typer.echo(bearer)
    typer.echo("This token is shown once and cannot be recovered -- store it now.")


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
    facets = _parse_facets(state, facet)
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
    task_state: str | None = typer.Option(
        None, "--task-state", help="open|done (T13.1); omit to leave the task state unchanged"
    ),
) -> None:
    """PATCH /v1/nodes/{id}.

    ``--task-state`` (spec §4.12) is only included in the request body when
    explicitly passed on the command line, mirroring the server's
    ``model_fields_set`` presence check (T13.1, ``api/routes/nodes.py``): an
    omitted flag must produce the exact same request body this command sent
    before T13.4, so a bare ``akasha set`` never accidentally clears/changes
    an existing task_state.
    """
    state = _state(ctx)
    if task_state is not None and task_state not in ("open", "done"):
        _usage_error(state, f"--task-state must be 'open' or 'done', got {task_state!r}")
    payload: dict[str, Any] = {
        "body": body,
        "change_class": change_class.value,
        "facets_touched": touch,
    }
    if task_state is not None:
        payload["task_state"] = task_state
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


# --- edge ------------------------------------------------------------------


@edge_app.command("add")
def edge_add(
    ctx: typer.Context,
    src: str,
    dst: str,
    edge_type: str = typer.Argument(
        ...,
        help="composes|supports|contradicts|depends_on|derived_from|cites|redirects_to",
    ),
    facet_binding: str | None = typer.Option(
        None,
        "--facet-binding",
        help="facet id on dst, or '*' -- required for justification edge types (spec §4.2)",
    ),
    facet_span: str | None = typer.Option(
        None,
        "--facet-span",
        help=(
            "highlighted span on dst -- mints a brand-new facet there and forces "
            "facet_binding to it, ignoring --facet-binding if also passed (task T7.7)"
        ),
    ),
    mode: str = typer.Option("track", "--mode", help="track|pin (default: track)"),
    pinned_commit: str | None = typer.Option(
        None, "--pinned-commit", help="commit hash to pin to (only meaningful with --mode pin)"
    ),
) -> None:
    """POST /v1/edges (spec §4.11, build-plan T14.2).

    Pure client of the endpoint: the facet-binding validation rule (spec
    §4.2 -- a justification edge type requires a non-``None``
    ``facet_binding``; ``None`` is legal only for ``composes``/
    ``redirects_to``) is enforced **server-side only**, exactly like
    ``--facet-span``'s facet-minting (T7.7) is server-side only. This verb
    never duplicates either rule; a violation reaches the caller as the
    server's own ``400 E_INVALID`` message, mapped by ``_exit_code_for``
    like every other ``E_INVALID`` response across this CLI (see
    ``_exit_code_for``'s docstring -- ``E_INVALID`` is not in the
    conflict/violation/needs-redirect bucket, so it is exit 1, matching
    every other validation error this CLI already surfaces, e.g. ``new``'s
    invalid ``node_type``).

    ``--facet-binding`` and ``--facet-span`` are mutually meaningful but
    not client-validated against each other -- both pass straight through
    to ``CreateEdgeBody`` and the server resolves precedence (a supplied
    ``facet_span`` always wins, forcing ``facet_binding`` to the newly
    minted facet's id).

    ``provenance`` is always ``"human"`` -- this CLI is a human-operated
    surface, the same value the Web UI's link form already sends
    (``ui/static/app.js``, task T14.6); it is not exposed as a flag
    (spec §4.12's CLI verb grammar does not name one, and ``provenance``
    is not a token-class distinction -- agent-token writes are rewritten
    into review-queue proposals by ``mutation_gate`` regardless of this
    field, see ``api/routes/edges.py``).
    """
    state = _state(ctx)
    payload: dict[str, Any] = {
        "src": src,
        "dst": dst,
        "edge_type": edge_type,
        "provenance": "human",
        "mode": mode,
    }
    if facet_binding is not None:
        payload["facet_binding"] = facet_binding
    if facet_span is not None:
        payload["facet_span"] = facet_span
    if pinned_commit is not None:
        payload["pinned_commit"] = pinned_commit
    result = _mutate(state, "POST", "/v1/edges", payload)
    _echo_ok(state, result)


@edge_app.command("rm")
def edge_rm(ctx: typer.Context, edge_id: str) -> None:
    """DELETE /v1/edges/{id} (spec §4.11, build-plan T14.2).

    A SOFT retract (``store.retract_edge`` sets ``retracted_at``; the
    source/target nodes are untouched and stay live) -- same semantics as
    the server route's own docstring.
    """
    state = _state(ctx)
    result = _mutate(state, "DELETE", f"/v1/edges/{edge_id}")
    _echo_ok(state, result)


# --- neighborhood/history ----------------------------------------------------


@app.command()
def neighborhood(
    ctx: typer.Context,
    node_id: str,
    hops: int = typer.Option(1, "--hops", help="expansion radius (default 1)"),
) -> None:
    """GET /v1/nodes/{id}/neighborhood?hops= (spec §4.11, build-plan T14.1).

    Read-only (does not use ``_mutate``/``--dry-run`` -- see
    ``tests/integration/test_cli_dry_run.py``'s AST meta-test, which only
    scans for mutating HTTP verbs). Plain (non-``--json``) output is one
    ASCII-only line per live edge: ``src -edge_type-> dst``, plus a
    trailing ``(facet: <facet_binding>)`` when the edge carries one --
    deliberately no graph-drawing/box characters (T9.9 Windows-console
    precedent, module header above).
    """
    state = _state(ctx)
    result = _request(
        state, "GET", f"/v1/nodes/{node_id}/neighborhood", params={"hops": hops}
    )
    if state.json_mode:
        _echo_ok(state, result)
        return
    for edge in result["edges"]:
        line = f"{edge['src']} -{edge['edge_type']}-> {edge['dst']}"
        if edge.get("facet_binding"):
            line += f" (facet: {edge['facet_binding']})"
        typer.echo(line)


@app.command()
def history(ctx: typer.Context, node_id: str) -> None:
    """GET /v1/nodes/{id}/history (spec §4.11, build-plan T14.1).

    Read-only (does not use ``_mutate``/``--dry-run``, same reasoning as
    ``neighborhood`` above). Plain (non-``--json``) output is one
    ASCII-only line per commit, oldest first (the endpoint's own order,
    spec §4.5 ``store.history``): ``hash change_class message ts``.
    """
    state = _state(ctx)
    result = _request(state, "GET", f"/v1/nodes/{node_id}/history")
    if state.json_mode:
        _echo_ok(state, result)
        return
    for commit in result["history"]:
        typer.echo(f"{commit['hash']} {commit['change_class']} {commit['message']} {commit['ts']}")


# --- review ------------------------------------------------------------------


@review_app.command("list")
def review_list(ctx: typer.Context, status: str = typer.Option("open", "--status")) -> None:
    """GET /v1/review?status=."""
    state = _state(ctx)
    result = _request(state, "GET", "/v1/review", params={"status": status})
    _echo_ok(state, result)


@review_app.command("resolve")
def review_resolve(ctx: typer.Context, review_id: str, resolution: str) -> None:
    """POST /v1/review/{id}/resolve (human only)."""
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
    """POST /v1/tokens (human only)."""
    state = _state(ctx)
    if token_class not in ("human", "agent"):
        _usage_error(state, f"--class must be 'human' or 'agent', got {token_class!r}")
    payload: dict[str, Any] = {"name": name, "token_class": token_class}
    if rate_per_min is not None:
        payload["rate_per_min"] = rate_per_min
    result = _mutate(state, "POST", "/v1/tokens", payload)
    _echo_ok(state, result)


@token_app.command("revoke")
def token_revoke(ctx: typer.Context, token_id: str) -> None:
    """DELETE /v1/tokens/{id} (human only)."""
    state = _state(ctx)
    result = _mutate(state, "DELETE", f"/v1/tokens/{token_id}")
    _echo_ok(state, result)


@token_app.command("list")
def token_list(ctx: typer.Context) -> None:
    """GET /v1/tokens (human only)."""
    state = _state(ctx)
    result = _request(state, "GET", "/v1/tokens")
    _echo_ok(state, result)


# --- sync ------------------------------------------------------------------


@sync_app.command("add")
def sync_add(
    ctx: typer.Context,
    path: str,
    name: str | None = typer.Option(None, "--name", help="sync root name (default: path basename)"),
) -> None:
    """POST /v1/sync/roots (task T4.10, human only).

    ``--name`` defaults to the path's basename when omitted -- a
    client-side convenience only, the server always receives an explicit,
    non-null ``name`` string (spec §4.11 unchanged request shape).
    """
    state = _state(ctx)
    root_name = name if name is not None else Path(path).name
    payload = {"name": root_name, "root_path": path}
    result = _mutate(state, "POST", "/v1/sync/roots", payload)
    _echo_ok(state, result)


# --- export ----------------------------------------------------------------


@app.command()
def export(
    ctx: typer.Context,
    md: str = typer.Option(
        ..., "--md", help="target directory to write canonical markdown into"
    ),
) -> None:
    """GET /v1/sync/export (task T10.2, spec §4.12).

    A pure client of the endpoint: writes each returned item's canonical
    ``text`` byte-for-byte to ``DIR/<sync_root>/<relative_path>`` (creating
    parent directories as needed), never re-encoding or adding/stripping a
    newline -- ``text`` is already the §4.7 canonical render, so writing it
    verbatim is what makes re-export byte-stable (T5.8). Prints a
    ``--json``-compatible summary of the files written plus the endpoint's
    ``unfiled_node_count``.
    """
    state = _state(ctx)
    result = _request(state, "GET", "/v1/sync/export")
    target_dir = Path(md)
    files_written: list[str] = []
    for item in result["items"]:
        dest = target_dir / item["sync_root"] / item["relative_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(item["text"].encode("utf-8"))
        files_written.append(str(dest))
    summary = {
        "files_written": files_written,
        "unfiled_node_count": result["unfiled_node_count"],
    }
    _echo_ok(state, summary)


if __name__ == "__main__":
    app()
