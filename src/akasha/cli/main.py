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
plus build-plan additions ``neighborhood``/``history`` (T14.1),
``edge add``/``edge rm`` (T14.2, both over the already-shipped
``POST /v1/edges``/``DELETE /v1/edges/{id}``, spec §4.11), and ``vet``
(T14.3, over the already-shipped ``POST /v1/nodes/{id}/vet``, spec §4.11).
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
``127.0.0.1:7433``. ``--token`` and ``--base-url`` also read the
``AKASHA_TOKEN`` / ``AKASHA_BASE_URL`` environment variables (build-plan
T18.2; flag > environment > default; ``daemon``/``init``/``tray`` ignore both).

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

import dataclasses
import io
import json as json_lib
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, NoReturn, cast

import httpx
import typer

from akasha import daemon as daemon_module
from akasha.api import auth
from akasha.config import (
    DEFAULT_BIND,
    DEFAULT_PORT,
    Config,
    default_config_dir,
    default_db_path,
    default_token_path,
    load_config,
    read_token,
    write_token,
)
from akasha.contract import grammar
from akasha.kernel import ids, store
from akasha.sync.watcher import detect_cloud_path

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
plugin_app = typer.Typer(add_completion=False, no_args_is_help=True)
app.add_typer(review_app, name="review")
app.add_typer(token_app, name="token")
app.add_typer(sync_app, name="sync")
app.add_typer(edge_app, name="edge")
app.add_typer(plugin_app, name="plugin")


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
    # build-plan T18.4: True only when NEITHER --base-url NOR AKASHA_BASE_URL was
    # supplied, i.e. the request targets the default local endpoint.
    default_endpoint: bool = False
    # build-plan T18.9: where the credential came from -- "flag", "env", "file" or "none".
    token_source: str = "none"


def _state(ctx: typer.Context) -> CliState:
    assert isinstance(ctx.obj, CliState)  # noqa: S101 - internal invariant, not user input
    return ctx.obj


@app.callback()
def main(
    ctx: typer.Context,
    base_url: str = typer.Option(
        DEFAULT_BASE_URL,
        "--base-url",
        envvar="AKASHA_BASE_URL",
        help="daemon base URL (default: spec §3 127.0.0.1:7433)",
    ),
    token: str | None = typer.Option(
        None, "--token", envvar="AKASHA_TOKEN", help="bearer token (or set AKASHA_TOKEN)"
    ),
    as_json: bool = typer.Option(
        False, "--json", help="emit versioned cli/v1 JSON instead of a plain body dump"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="mutating verbs print the would-be request and exit 0 without sending it",
    ),
) -> None:
    # build-plan T18.2: precedence is explicit flag > environment > default (typer's
    # own). An exported-but-empty AKASHA_TOKEN / AKASHA_BASE_URL must behave as unset:
    # an empty bearer would only ever produce a confusing 401.
    source = ctx.get_parameter_source("base_url")
    # typer vendors its own click, so compare the source by name, not by enum identity.
    default_endpoint = not base_url or source is None or source.name == "DEFAULT"
    if default_endpoint:
        # The default endpoint is the address the default config's daemon serves on
        # (equal to DEFAULT_BASE_URL absent a config), so a config with another port
        # is honoured without a flag -- build-plan T18.9 step 4, pulled forward
        # because on-demand start (T18.4) is only testable/correct with it.
        base_url = daemon_module.health_url(load_config(None))
    resolved_token = token or None
    token_source = "none"
    if resolved_token is not None:
        token_param_source = ctx.get_parameter_source("token")
        is_flag = token_param_source is not None and token_param_source.name == "COMMANDLINE"
        token_source = "flag" if is_flag else "env"
    elif default_endpoint:
        # T18.9 (ruling M18-A): flag > env > the saved token file. Only ever for the
        # default local endpoint -- the saved secret is never sent to an address the
        # user named explicitly (pass --token for those).
        resolved_token = read_token(default_token_path())
        token_source = "file" if resolved_token else "none"
    ctx.obj = CliState(
        base_url=base_url.rstrip("/"),
        token=resolved_token,
        json_mode=as_json,
        dry_run=dry_run,
        default_endpoint=default_endpoint,
        token_source=token_source,
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


def _autostart_daemon(state: CliState) -> bool:
    """Start the default local daemon on demand after a refused connection (T18.4).

    Only for the default endpoint (never an explicit --base-url / AKASHA_BASE_URL:
    test daemons and remote hosts are not ours to spawn), never with
    ``AKASHA_NO_AUTOSTART`` set. Never silent: one line on stderr. Safe because startup
    reconcile is idempotent (spec §4.8), so anything edited while the daemon was
    down is picked up on start. Returns True iff a daemon was started.
    """
    if not state.default_endpoint or os.environ.get("AKASHA_NO_AUTOSTART"):
        return False
    result = daemon_module.up(load_config(None))
    if result.status != "started":
        return False
    typer.echo(f"started daemon (log: {result.log_path})", err=True)
    return True


def _request(
    state: CliState,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    missing_ok: bool = False,
) -> Any:
    headers = {"Authorization": f"Bearer {state.token}"} if state.token else {}

    def send() -> httpx.Response:
        return httpx.request(
            method,
            f"{state.base_url}{path}",
            params=params,
            json=json_body,
            headers=headers,
            timeout=10.0,
        )

    try:
        try:
            resp = send()
        except httpx.ConnectError:
            if not _autostart_daemon(state):
                raise
            resp = send()  # retried exactly once, and only after we started the daemon
    except httpx.HTTPError as exc:
        _fail(state, 0, "E_CONNECTION", f"could not reach daemon at {state.base_url}: {exc}", {})
    if missing_ok and resp.status_code == 404:
        return None  # the caller reports it (e.g. `render`'s unresolved marker)
    if resp.status_code >= 400:
        code, message, detail = _parse_error_body(resp)
        hint: str | None = None
        if resp.status_code == 401 and not state.token:
            hint = (
                "no credential was supplied: run `akasha setup` to create one, then "
                "`export AKASHA_TOKEN=...` (or pass --token)"
            )
        elif resp.status_code == 401 and state.token_source == "file":
            hint = (
                f"the saved token ({default_token_path()}) was rejected -- it may have been "
                "revoked, or the database was reset; pass --token/AKASHA_TOKEN with a valid one"
            )
        if hint is not None:
            if state.json_mode:
                detail = {**detail, "hint": hint}
            else:
                message = f"{message}\nhint: {hint}"
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
def up(
    config: str | None = typer.Option(
        None, "--config", help="path to config.toml (default: per-OS default location)"
    ),
) -> None:
    """Start the daemon detached and wait until it is healthy (build-plan T18.3).

    Idempotent: if the daemon already answers at the config's address, nothing is
    spawned. Like ``daemon`` and ``init`` this is a process verb, not an HTTP
    client -- it ignores ``--base-url``/``--token``. The daemon writes its own
    rotating log in the config directory; the path is printed. Exit 4 if the
    single-instance lock is held but nothing answers (something else owns it),
    1 if the daemon did not become healthy in time.
    """
    cfg = load_config(config)
    result = daemon_module.up(cfg)
    if result.status == "already":
        typer.echo(f"daemon already running at {result.url} (log: {result.log_path})")
    elif result.status == "started":
        typer.echo(f"started daemon at {result.url} (log: {result.log_path})")
    elif result.status == "conflict":
        typer.echo(
            f"error: the daemon lock is held but nothing answers at {result.url}; "
            f"see {result.log_path}",
            err=True,
        )
        raise typer.Exit(4)
    else:
        typer.echo(
            f"error: daemon did not become healthy at {result.url}; see {result.log_path}",
            err=True,
        )
        raise typer.Exit(1)


@app.command()
def down(
    config: str | None = typer.Option(
        None, "--config", help="path to config.toml (default: per-OS default location)"
    ),
) -> None:
    """Stop the detached daemon (build-plan T18.3). Idempotent.

    "Running" means the single-instance lock is held, not that a pid file exists,
    so a stale pid file is removed and never signalled. Stopping abruptly is safe:
    startup reconcile is idempotent (spec §4.8), so anything edited meanwhile is
    picked up on the next start. Exit 1 if it did not stop in time.
    """
    cfg = load_config(config)
    outcome = daemon_module.down(cfg)
    if outcome == "timeout":
        typer.echo("error: the daemon did not stop in time", err=True)
        raise typer.Exit(1)
    typer.echo("stopped daemon" if outcome == "stopped" else "daemon is not running")


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


def _open_migrated_db(cfg: Config) -> sqlite3.Connection:
    """Open (creating the directory if needed) and migrate the config's database.

    Shared by ``init`` and ``setup`` (build-plan T18.5): one bootstrap path, no
    second way to reach a fresh DB.
    """
    db_path = cfg.db_path if cfg.db_path is not None else default_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = store.connect(db_path, check_same_thread=False)
    store.run_migrations(conn)
    return conn


def _save_token(cfg: Config, bearer: str) -> tuple[Path, bool]:
    """Save the freshly minted human token beside the config (0600); never overwrite.

    Returns ``(path, saved)``: ``saved`` is False when a token file already existed
    and was left untouched (ruling M18-A: an existing file is never clobbered).
    """
    config_dir = Path(cfg.path).parent if cfg.path is not None else default_config_dir()
    path = default_token_path(config_dir)
    if path.exists():
        return path, False
    write_token(path, bearer)
    return path, True


def _mint_human_token(conn: sqlite3.Connection, name: str) -> str:
    """Mint one human-class token and return its bearer string (shown once by callers)."""
    raw_secret = auth.mint_secret()
    token = store.create_token(conn, name, "human", auth.hash_secret(raw_secret))
    return auth.format_bearer_token(token["id"], raw_secret)


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
    conn = _open_migrated_db(cfg)

    if store.list_tokens(conn):
        typer.echo(
            "error: a token already exists; use the running daemon's "
            "POST /v1/tokens (human token required) to mint another",
            err=True,
        )
        raise typer.Exit(4)

    bearer = _mint_human_token(conn, name)
    typer.echo(bearer)
    typer.echo("This token is shown once and cannot be recovered -- store it now.")
    path, saved = _save_token(cfg, bearer)
    typer.echo(
        f"saved to {path} (readable only by you)" if saved else f"left {path} untouched",
        err=True,
    )


def _register_vault(state: CliState, vault: Path, name: str) -> dict[str, Any]:
    """Register ``vault`` as a sync root and reconcile it once (both human-only)."""
    _mutate(state, "POST", "/v1/sync/roots", {"name": name, "root_path": str(vault)})
    summary: dict[str, Any] = _mutate(state, "POST", "/v1/sync/rescan", None)
    return summary


@app.command()
def setup(
    ctx: typer.Context,
    vault: str | None = typer.Argument(None, help="a folder of Markdown notes to sync (optional)"),
    config: str | None = typer.Option(
        None, "--config", help="path to config.toml (default: per-OS default location)"
    ),
    name: str | None = typer.Option(None, "--name", help="sync root name (default: folder name)"),
) -> None:
    """Nothing to a live, syncing vault in one command (build-plan T18.5).

    Creates the database and your first human token on a fresh install (or, if a
    token already exists, needs it via ``--token``/``AKASHA_TOKEN`` -- a token
    cannot be recovered), starts the daemon in the background, and -- given a
    VAULT folder -- registers it and reconciles it once. Every Markdown file under
    the vault is tracked by default; a ``.tmignore`` file at its root opts paths
    out. Safe to re-run. Like ``init``/``up`` this is a process verb: it ignores
    ``--base-url`` and talks to the address in the config. ``--dry-run`` prints the
    plan and changes nothing (no token, no daemon, no registration).
    """
    state = _state(ctx)
    cfg = load_config(config)
    root = Path(vault).expanduser().resolve() if vault is not None else None
    root_name = name if name is not None else (root.name if root is not None else None)
    url = daemon_module.health_url(cfg)

    if state.dry_run:
        plan = ["ensure the database and a human token", f"start the daemon at {url}"]
        if root is not None:
            plan += [f"register {root} as {root_name!r}", "reconcile it once"]
        if state.json_mode:
            envelope = {"schema": CLI_SCHEMA, "ok": True, "dry_run": True, "plan": plan}
            typer.echo(json_lib.dumps(envelope))
        else:
            typer.echo("dry run -- nothing was changed. Would:")
            for step in plan:
                typer.echo(f"  - {step}")
        raise typer.Exit(0)

    if root is not None and not root.is_dir():
        typer.echo(f"error: {root} is not a folder", err=True)
        raise typer.Exit(3)

    conn = _open_migrated_db(cfg)
    minted: str | None = None
    token_path = default_token_path()
    token_saved = False
    if store.list_tokens(conn):
        bearer = state.token
        if not bearer:
            typer.echo(
                "error: a token already exists and none was supplied. A token cannot be "
                "recovered: pass --token or set AKASHA_TOKEN to it, or (with a running "
                "daemon) mint another with `akasha token create`.",
                err=True,
            )
            raise typer.Exit(4)
    else:
        minted = bearer = _mint_human_token(conn, "bootstrap")
        token_path, token_saved = _save_token(cfg, minted)
    conn.close()

    up_result = daemon_module.up(cfg)
    if up_result.status == "conflict":
        typer.echo(f"error: the daemon lock is held but nothing answers at {url}", err=True)
        raise typer.Exit(4)
    if up_result.status == "failed":
        typer.echo(
            f"error: daemon did not become healthy at {url}; see {up_result.log_path}", err=True
        )
        raise typer.Exit(1)

    summary: dict[str, Any] | None = None
    warning: str | None = None
    if root is not None:
        assert root_name is not None
        api = CliState(base_url=url, token=bearer, json_mode=state.json_mode, dry_run=False)
        summary = _register_vault(api, root, root_name)
        provider = detect_cloud_path(str(root))
        if provider is not None:
            warning = (
                f"this folder is under {provider}: sync clients can rewrite files "
                "behind akasha's back, so it runs in its cautious profile"
            )

    if state.json_mode:
        data: dict[str, Any] = {
            "daemon": {"url": url, "status": up_result.status, "log": str(up_result.log_path)},
            "vault": None if root is None else {"path": str(root), "name": root_name},
            "rescan": summary,
            "token": minted,
        }
        typer.echo(json_lib.dumps({"schema": CLI_SCHEMA, "ok": True, "data": data}))
        return

    typer.echo(f"daemon: {url} ({up_result.status}; log: {up_result.log_path})")
    if root is not None:
        files = (summary or {}).get("files_reconciled", 0)
        typer.echo(f"vault:  {root} registered as {root_name!r}; {files} file(s) reconciled")
        typer.echo(
            "        every Markdown file in it is tracked by default; add a .tmignore file "
            "at its root to opt paths out"
        )
        typer.echo(
            "        transclusion is live: the same ^tm- id in several notes is one line kept "
            "identical in all of them.\n"
            '        Try it: write "- [ ] something ^tm-new" (or only a part, '
            '"{something}{tm-new}", which may span lines) in a note, then copy it (id '
            "included) into another."
        )
        if warning:
            typer.echo(f"warning: {warning}")
    if minted is not None:
        typer.echo("")
        if token_saved:
            typer.echo(f"saved your token to {token_path} (readable only by you)")
        else:
            typer.echo(f"note: left the existing token file {token_path} untouched")
        typer.echo(f"your token (shown once, it cannot be recovered):\n  {minted}")
        typer.echo(f"  export AKASHA_TOKEN={minted}")
        typer.echo(f"web UI: {url}/?token={minted}")
        typer.echo(
            "warning: that link and token are secrets -- anyone who has them can act as you; "
            "keep them out of shared terminals and browser history you sync"
        )
    elif root is None:
        typer.echo("already set up. Give a folder to sync it: `akasha setup <vault>`")


def _group_reviews(items: list[dict[str, Any]]) -> dict[str, int]:
    """Count review items by violation code (a pause has no code: it is grouped as "pause")."""
    counts: dict[str, int] = {}
    for item in items:
        code = "pause"
        try:
            ref: Any = json_lib.loads(item.get("cause_ref") or "{}")
        except ValueError:
            ref = {}
        if isinstance(ref, dict):
            detail = cast("dict[str, Any]", ref)
            code = "pause" if detail.get("pause") else str(detail.get("code", "violation"))
        counts[code] = counts.get(code, 0) + 1
    return counts


@app.command()
def status(ctx: typer.Context) -> None:
    """One screen: why is (or isn't) my vault syncing? (build-plan T18.6). Read-only.

    Reports whether the daemon answers (version), whether your credential is
    accepted, each sync root with how many files have something tracked, open
    violations/pauses by code and the open-review count -- and names the three
    classic first-run failures with a one-line fix each: no credential, no vault
    registered, and a registered vault with nothing tracked. Issues only GETs and
    never starts a daemon (a diagnosis should not change what it diagnoses). Exit 0
    when healthy; an unreachable daemon or rejected credential uses the shared
    error mapping.
    """
    state = dataclasses.replace(_state(ctx), default_endpoint=False)  # never autostart here
    health: dict[str, Any] = _request(state, "GET", "/health")
    hints: list[str] = []
    data: dict[str, Any] = {
        "daemon": {
            "url": state.base_url,
            "version": health.get("version"),
            "contract_version": health.get("contract_version"),
        },
        "token": "none" if not state.token else "accepted",
    }
    if not state.token:
        hints.append(
            "no credential was supplied: run `akasha setup` if this is a fresh install, "
            "then `export AKASHA_TOKEN=...` (or pass --token)"
        )
        data["hints"] = hints
        _print_status(state, data)
        raise typer.Exit(1)
    sync: dict[str, Any] = _request(state, "GET", "/v1/sync/status")
    reviews: dict[str, Any] = _request(state, "GET", "/v1/review", params={"status": "open"})
    roots: list[dict[str, Any]] = sync.get("sync_roots", [])
    data["sync_roots"] = [
        {
            "name": r["name"],
            "path": r["root_path"],
            "files_tracked": len(r["files"]),
            "violations": _group_reviews(r["violations"]),
            "pauses": len(r["pauses"]),
            "conflicts": len(r["conflicts"]),
        }
        for r in roots
    ]
    data["open_reviews"] = len(reviews.get("reviews", []))
    if not roots:
        hints.append("no vault is registered: run `akasha setup <folder-of-notes>`")
    for r in data["sync_roots"]:
        if r["files_tracked"] == 0:
            hints.append(
                f"{r['path']} is registered but nothing in it is tracked yet: notes only "
                "sync once they hold a task or block to project (add `- [ ] something "
                "^tm-new`); also check the path and any .tmignore"
            )
    data["hints"] = hints
    _print_status(state, data)


def _print_status(state: CliState, data: dict[str, Any]) -> None:
    if state.json_mode:
        _echo_ok(state, data)
        return
    d = data["daemon"]
    typer.echo(
        f"daemon:  ok  {d['url']}  (version {d['version']}, contract {d['contract_version']})"
    )
    typer.echo(f"token:   {'not supplied' if data['token'] == 'none' else 'accepted'}")
    if "sync_roots" in data:
        typer.echo(f"sync roots: {len(data['sync_roots'])}")
        for r in data["sync_roots"]:
            typer.echo(f"  {r['path']}  [{r['name']}]")
            typer.echo(
                f"    files tracked: {r['files_tracked']}  pauses: {r['pauses']}  "
                f"conflicts: {r['conflicts']}"
            )
            for code, n in sorted(r["violations"].items()):
                typer.echo(f"    violation {code}: {n}")
        typer.echo(f"open reviews: {data['open_reviews']}  (akasha review list)")
    for hint in data["hints"]:
        typer.echo(f"hint: {hint}")


def _resolve_embed(
    state: CliState, node_id: str, cache: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Look one embed target up (read-only); cached per call. Never raises for a missing node."""
    if node_id not in cache:
        node: dict[str, Any] | None = _request(
            state, "GET", f"/v1/nodes/{node_id}", missing_ok=True
        )
        if node is None:
            cache[node_id] = {"id": node_id, "status": "missing", "body": None}
        else:
            cache[node_id] = {
                "id": node_id,
                "status": node.get("status", "live"),
                "body": (node.get("body") or "").rstrip("\n"),  # bodies end in a newline
                "task_state": node.get("task_state"),
            }
    return cache[node_id]


def _embed_text(target: dict[str, Any]) -> str:
    if target["status"] != "live" or target["body"] is None:
        return f"[unresolved: ^tm-{target['id']} ({target['status']})]"
    mark = {"open": "[ ] ", "done": "[x] "}.get(target.get("task_state") or "", "")
    return f"{mark}{target['body']}"


@app.command(name="render")
def render_file(ctx: typer.Context, file: str) -> None:
    """Show a file with each embed replaced by its target's CURRENT text (build-plan T18.7).

    A viewer, not propagation: this does not modify files. On disk an embed is a
    link (`![[path#^tm-id]]`) and stays one; this prints what Obsidian would show.
    A standalone embed line becomes a quoted block labelled with its id and source
    path; an inline embed is replaced by the quoted text. A tombstoned or missing
    target prints `[unresolved: ...]` rather than vanishing. `--json` lists every
    embed with its resolved body and state.
    """
    state = _state(ctx)
    path = Path(file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"error: cannot read {file}: {exc}", err=True)
        raise typer.Exit(3) from exc
    cache: dict[str, dict[str, Any]] = {}
    out_lines: list[str] = []
    embeds: list[dict[str, Any]] = []
    in_fence = False
    for line_no, line in enumerate(text.split("\n"), start=1):
        if grammar.FENCE_RE.match(line):
            in_fence = not in_fence
        if in_fence or "![[" not in line:
            out_lines.append(line)
            continue
        standalone = grammar.EMBED_RE.fullmatch(line)

        def _sub(m: re.Match[str], line_no: int = line_no) -> str:
            target = _resolve_embed(state, m.group("id"), cache)
            embeds.append({"line": line_no, "path": m.group("path"), **target})
            body = _embed_text(target)
            return body if standalone else f'"{body}" (^tm-{m.group("id")})'

        rendered = grammar.EMBED_RE.sub(_sub, line)
        if standalone:
            m = standalone
            label = f"(^tm-{m.group('id')}, from {m.group('path')})"
            out_lines.append(f"> {rendered.replace(chr(10), chr(10) + '> ')} {label}")
        else:
            out_lines.append(rendered)
    rendered_text = "\n".join(out_lines)
    if state.json_mode:
        _echo_ok(state, {"file": str(path), "embeds": embeds, "text": rendered_text})
    else:
        typer.echo(rendered_text, nl=not rendered_text.endswith("\n"))


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


# PRD R9 ("system language says 'vetted by you,' never 'true' (including MCP
# responses)"): the docstring below is rendered verbatim by `--help`, so the
# forbidden word itself must never appear there -- it is only spelled out in
# this (non-rendered) comment and in the SPEC-QUESTION below, never in the
# docstring or in the plain-output f-string.
@app.command()
def vet(ctx: typer.Context, node_id: str) -> None:
    """POST /v1/nodes/{id}/vet -- the S4 human act (spec §4.11, build-plan T14.3).

    ``/vet`` is ``require_human``/exempt (spec §4.6, §4.11): unlike every
    other mutating verb, an agent-class token is never proposalized here --
    it gets the server's own ``403 E_HUMAN_ONLY``, surfaced through the
    existing ``_exit_code_for`` mapping (no client-side token-class check
    is added; see that function's own SPEC-QUESTION docstring, which this
    verb deliberately leaves untouched).

    Plain (non-``--json``) output says the node was vetted by you --
    vetting is a claim about your own review, not a claim about the world
    (PRD R9). ``--json`` returns the real, unmodified API response for
    scripted callers -- see the ``SPEC-QUESTION`` comment on the
    ``if state.json_mode`` branch below for the one open question that
    leaves unresolved.
    """
    state = _state(ctx)
    result = _mutate(state, "POST", f"/v1/nodes/{node_id}/vet", None)
    if state.json_mode:
        # SPEC-QUESTION (T14.3): PRD R9's parenthetical ("including MCP
        # responses") explicitly extends the "never say the forbidden word"
        # rule to at least one machine-facing surface, and the API's
        # `vetted` field is a literal JSON boolean -- so a freshly-vetted
        # node's `--json` output here necessarily contains that literal
        # token. Narrowest reading taken here: `--json` is this CLI's
        # documented, versioned wire contract (`cli/v1`), the same contract
        # every other verb's `--json` mode gives a scripted caller
        # unmodified (contrast the MCP surface R9 names, which speaks
        # generated prose to a model, not a fixed JSON schema to a script)
        # -- so it passes the real API response through verbatim rather
        # than inventing a divergent response shape for this one verb
        # (rule 0.2). This is a genuine open question, not settled by this
        # task's narrowest reading alone -- see docs/spec-questions.md
        # T14.3 for the human ruling needed.
        typer.echo(json_lib.dumps({"schema": CLI_SCHEMA, "ok": True, "data": result}))
    else:
        maturity = result.get("maturity", "?")
        typer.echo(f"{node_id}: vetted by you (maturity: {maturity})")


# --- split / merge -----------------------------------------------------------


def _count_reassignment_reviews(state: CliState, old_id: str) -> int:
    """Open ``reassignment`` review items that a split of ``old_id`` queued (read-only)."""
    body: dict[str, Any] = _request(state, "GET", "/v1/review", params={"status": "open"})
    reviews: list[dict[str, Any]] = body.get("reviews", [])
    count = 0
    for item in reviews:
        if item.get("cause_kind") != "reassignment":
            continue
        try:
            ref: Any = json_lib.loads(item.get("cause_ref") or "{}")
        except ValueError:
            continue
        if isinstance(ref, dict) and cast("dict[str, Any]", ref).get("old_id") == old_id:
            count += 1
    return count


def _echo_refactor_proposed(result: dict[str, Any], verb: str) -> None:
    review: dict[str, Any] = result.get("review") or {}
    typer.echo(
        f"{verb} proposed for human review (review {review.get('id', '?')}); "
        "nothing changed yet. Approve it with `akasha review resolve`."
    )


@app.command()
def split(
    ctx: typer.Context,
    node_id: str,
    part: list[str] = typer.Option(
        [], "--part", help="TYPE=BODY, repeatable (one new node per part, in order)"
    ),
) -> None:
    """POST /v1/nodes/{id}/split -- replace one node by several (build-plan T14.4).

    Every ``--part`` becomes a brand-new node; the original id is tombstoned
    with a redirect to them, and every live inbound edge is moved to the FIRST
    successor so nothing dangles. One ``reassignment`` review item is opened per
    inbound edge so you can decide which successor each edge really belongs to
    (``akasha review list``; resolve with ``akasha review resolve <id> still_holds``).
    """
    state = _state(ctx)
    parts: list[dict[str, Any]] = []
    for item in part:
        node_type, sep, body = item.partition("=")
        if not sep or not node_type or not body:
            _usage_error(state, f"--part must be TYPE=BODY, got {item!r}")
        parts.append({"node_type": node_type, "body": body})
    if not parts:
        _usage_error(state, "split needs at least one --part TYPE=BODY")
    result = _mutate(state, "POST", f"/v1/nodes/{node_id}/split", {"parts": parts})
    if state.json_mode:
        _echo_ok(state, result)
        return
    if result.get("proposed"):
        _echo_refactor_proposed(result, "split")
        return
    successors: list[str] = result.get("redirect", {}).get(node_id, [])
    queued = _count_reassignment_reviews(state, node_id)
    typer.echo(f"split {node_id} into {', '.join(successors)}")
    typer.echo(f"redirect: {node_id} -> {', '.join(successors)} (no id is left dangling)")
    typer.echo(
        f"{queued} reassignment review item(s) opened for inbound edges "
        f"(they were moved to {successors[0] if successors else '?'}); "
        "see `akasha review list`"
    )


@app.command()
def merge(
    ctx: typer.Context,
    node_id: str,
    other_ids: list[str] = typer.Argument(..., help="node ids to merge INTO node_id"),
) -> None:
    """POST /v1/nodes/{id}/merge -- fold other nodes into ``node_id`` (build-plan T14.4).

    ``node_id`` (the path id) survives; each other id is tombstoned with a
    redirect to it and every live inbound edge is moved to the survivor, so
    nothing dangles. Unlike ``split`` a merge has one unambiguous survivor, so
    it opens no reassignment review items.
    """
    state = _state(ctx)
    result = _mutate(state, "POST", f"/v1/nodes/{node_id}/merge", {"ids": list(other_ids)})
    if state.json_mode:
        _echo_ok(state, result)
        return
    if result.get("proposed"):
        _echo_refactor_proposed(result, "merge")
        return
    redirect: dict[str, list[str]] = result.get("redirect", {})
    typer.echo(f"merged {', '.join(redirect)} into {node_id} (the survivor)")
    for old_id in redirect:
        typer.echo(f"redirect: {old_id} -> {node_id} (no id is left dangling)")
    typer.echo("no reassignment review items: a merge has a single survivor")


# --- plugin ------------------------------------------------------------------

PLUGIN_ID = "tm-hub"  # plugin-obsidian/manifest.json "id"


def _default_plugin_dir() -> Path | None:
    """The built ``plugin-obsidian/`` of a source checkout (an installed wheel has none)."""
    candidate = Path(__file__).resolve().parents[3] / "plugin-obsidian"
    return candidate if (candidate / "manifest.json").is_file() else None


def _write_if_changed(path: Path, data: bytes, ops: list[str], *, dry_run: bool) -> None:
    existing = path.read_bytes() if path.is_file() else None
    if existing == data:
        ops.append(f"unchanged  {path}")
        return
    ops.append(f"{'would write' if dry_run else 'wrote'}  {path}")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


@plugin_app.command("install")
def plugin_install(
    ctx: typer.Context,
    vault: str,
    from_dir: str | None = typer.Option(
        None, "--from", help="a BUILT plugin-obsidian/ directory (default: this checkout's)"
    ),
    config: str | None = typer.Option(
        None, "--config", help="path to config.toml, to learn the daemon address"
    ),
    with_token: bool = typer.Option(
        False,
        "--with-token",
        help="also write your token into the plugin's settings (opt in; see the warning)",
    ),
) -> None:
    """Install the Obsidian plugin into VAULT/.obsidian/plugins/tm-hub (build-plan T18.8).

    Copies ``manifest.json`` and the built ``main.js``, records the daemon address
    in the plugin's ``data.json`` (only when absent) and lists the plugin in
    ``community-plugins.json``. The token is written ONLY with ``--with-token`` --
    it then sits in a file inside the vault, so a warning is printed when the vault
    is under OneDrive/Dropbox or is a git repository. Idempotent, and ``--dry-run``
    prints the file operations without writing. Obsidian's own consent step cannot
    be automated: turn off Restricted mode once, enable the plugin (and paste your
    token in its settings unless you used ``--with-token``) -- the command prints
    exactly that.
    """
    state = _state(ctx)
    if with_token and not state.token:
        typer.echo(
            "error: --with-token needs a token: run `akasha setup`, or pass --token/AKASHA_TOKEN",
            err=True,
        )
        raise typer.Exit(4)
    vault_dir = Path(vault).expanduser().resolve()
    if not vault_dir.is_dir():
        typer.echo(f"error: {vault_dir} is not a folder", err=True)
        raise typer.Exit(3)
    src = Path(from_dir).expanduser().resolve() if from_dir else _default_plugin_dir()
    if src is None:
        typer.echo(
            "error: pass --from DIR, a built plugin-obsidian/ "
            "(run `npm ci && npm run build` in plugin-obsidian/ first)",
            err=True,
        )
        raise typer.Exit(3)
    if not (src / "manifest.json").is_file() or not (src / "main.js").is_file():
        typer.echo(
            f"error: {src} has no built plugin (manifest.json + main.js): "
            "run `npm ci && npm run build` in plugin-obsidian/ first",
            err=True,
        )
        raise typer.Exit(3)

    plugin_dir = vault_dir / ".obsidian" / "plugins" / PLUGIN_ID
    ops: list[str] = []
    dry = state.dry_run
    _write_if_changed(plugin_dir / "manifest.json", (src / "manifest.json").read_bytes(), ops,
                      dry_run=dry)  # fmt: skip
    _write_if_changed(plugin_dir / "main.js", (src / "main.js").read_bytes(), ops, dry_run=dry)

    data_path = plugin_dir / "data.json"
    settings: dict[str, Any] = {}
    if data_path.is_file():
        try:
            loaded: Any = json_lib.loads(data_path.read_text(encoding="utf-8"))
        except ValueError:
            typer.echo(f"error: {data_path} is not valid JSON; not touching it", err=True)
            raise typer.Exit(1) from None
        if not isinstance(loaded, dict):
            typer.echo(f"error: {data_path} is not a JSON object; not touching it", err=True)
            raise typer.Exit(1)
        settings = cast("dict[str, Any]", loaded)
    settings.setdefault("daemonUrl", daemon_module.health_url(load_config(config)))
    if with_token:
        # The plugin's setting is `apiToken` (plugin-obsidian/src/settings.ts).
        settings["apiToken"] = state.token
        provider = detect_cloud_path(str(vault_dir))
        if provider is not None or (vault_dir / ".git").exists():
            where = f"under {provider}" if provider else "a git repository"
            typer.echo(
                f"warning: this vault is {where}; the token in {data_path} will be synced or "
                "committed with it -- keep it out of anything shared",
                err=True,
            )
    _write_if_changed(data_path, (json_lib.dumps(settings, indent=2) + "\n").encode(), ops,
                      dry_run=dry)  # fmt: skip

    listing_path = vault_dir / ".obsidian" / "community-plugins.json"
    enabled: list[Any] = []
    if listing_path.is_file():
        try:
            parsed: Any = json_lib.loads(listing_path.read_text(encoding="utf-8"))
        except ValueError:
            typer.echo(f"error: {listing_path} is not valid JSON; not touching it", err=True)
            raise typer.Exit(1) from None
        enabled = cast("list[Any]", parsed) if isinstance(parsed, list) else []
    if PLUGIN_ID not in enabled:
        enabled.append(PLUGIN_ID)
    _write_if_changed(listing_path, (json_lib.dumps(enabled, indent=2) + "\n").encode(), ops,
                      dry_run=dry)  # fmt: skip

    if state.json_mode:
        _echo_ok(state, {"dry_run": dry, "operations": ops})
        return
    for op in ops:
        typer.echo(op)
    if dry:
        typer.echo("dry run -- nothing was written")
        return
    typer.echo("")
    typer.echo("Left for you in Obsidian (its consent step cannot be automated):")
    typer.echo("  1. Settings > Community plugins > turn off Restricted mode (once)")
    typer.echo("  2. enable 'TM Hub'")
    if not with_token:
        typer.echo("  3. Settings > TM Hub > API token: paste your token")


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
