"""Sync status + rescan routes (task T5.7, spec §4.11 ``/sync/status``, ``/sync/rescan``).

These are the two ``/sync/*`` rows deferred from M4 (durable sync-*root*
registration itself is T4.10's ``routes/sync_roots.py``, unaffected by this
file). Neither row carries a ``∅`` (human-only) marker in the §4.11 table,
so both endpoints use ``require_auth`` (any authenticated token class), NOT
``require_human`` — narrowest literal reading of the table.

``POST /sync/rescan`` is deliberately NOT wired through T4.6's
``mutation_gate``: that gate exists to rewrite an *agent's own* mutating
request body into a proposal for a human to approve (spec §4.11 intro:
"agent-class tokens: mutating endpoints are rewritten into proposals").
A rescan is an operational trigger with no request-body content of its
own — it re-runs the same idempotent §4.8 reconcile pipeline that the
daemon's file-watcher already runs continuously and that startup/crash
recovery (T5.6) runs unconditionally, applying vault<->hub convergence as
``author="sync"`` independent of which token triggered it. Proposalizing
it would mean an agent token could never even ask the daemon to converge,
which isn't what "mutating endpoints become proposals" is protecting
against (there is no free-form authored content here to gate). Narrowest
reading: ``require_auth`` only.

``GET /sync/status`` is read-only: it never writes to SQLite from this
module (rule 0.4's writes-go-through-store.py discipline is trivially
satisfied here because there are no writes at all) and reuses T5.4/T5.5/
T4.10 read helpers verbatim (``store.list_sync_roots``,
``store.list_sync_files``, ``store.find_open_reviews``) rather than
inventing a new query.

Pause detection: a "pause" is not a distinct ``cause_kind`` (spec/M3
follow-up, ``docs/agents/task-status.md`` M5 preamble point 2) — it is an
open ``cause_kind='violation'`` review whose ``cause_ref`` JSON carries
``"pause": true`` (see ``sync/reconcile.py``'s ``on_change``, the
``linter.pause_and_diff`` branch). This module splits violations from
pauses by parsing that JSON, never by branching on any borrowed
``ViolationCode``/``PauseDecision.review_item.code`` (the same guardrail
T3.6/T5.4 documented).

Root correlation: every violation/conflict ``cause_ref`` reconcile writes
embeds the file ``path`` it concerns (see ``reconcile.py``'s
``store.enqueue_review(..., cause_ref=canonical_json({"path": path, ...}))``
call sites for both the violation and conflict branches). This module
looks that path up in ``store.list_sync_files`` to find the owning
``sync_root_id`` and bucket the review under that root. A review whose
``cause_ref`` has no resolvable path (or a path that isn't presently a
tracked ``sync_files`` row) is not silently dropped — it lands in a
top-level ``"unresolved"`` bucket instead.
"""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, Depends

from akasha.api import auth
from akasha.api.deps import get_conn, require_auth
from akasha.kernel import store
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import Reconciler

router = APIRouter(prefix="/v1/sync", tags=["sync"])


def _parse_cause_ref(cause_ref: str | None) -> dict[str, Any]:
    """Best-effort parse of a review's ``cause_ref`` JSON to a dict.

    Every write site in ``reconcile.py`` writes ``cause_ref`` via
    ``kernel.canonical.canonical_json`` on a dict (never pickle/eval, rule
    0.5), so this is always well-formed JSON in practice; the fallbacks
    below only guard against an unrelated/legacy ``cause_kind`` (e.g. a
    future M7 trigger) whose ``cause_ref`` isn't a JSON object, so a
    single malformed row can never crash the whole status endpoint.
    """
    if not cause_ref:
        return {}
    try:
        parsed: Any = json.loads(cause_ref)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return cast("dict[str, Any]", parsed)


@router.get("/status")
def sync_status(
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Per-sync-root state: files, open violations, pauses, conflicts (spec §4.11)."""
    roots = store.list_sync_roots(conn)
    files = store.list_sync_files(conn)

    root_entries: dict[str, dict[str, Any]] = {
        root["id"]: {
            "id": root["id"],
            "name": root["name"],
            "root_path": root["root_path"],
            "files": [],
            "violations": [],
            "pauses": [],
            "conflicts": [],
        }
        for root in roots
    }

    file_by_path: dict[str, dict[str, Any]] = {f["path"]: f for f in files}
    for f in files:
        entry = root_entries.get(f["sync_root_id"])
        if entry is not None:
            entry["files"].append(
                {
                    "path": f["path"],
                    "contract_version": f["contract_version"],
                    "last_synced_at": f["last_synced_at"],
                }
            )

    unresolved: list[dict[str, Any]] = []

    def _root_entry_for_path(path: str | None) -> dict[str, Any] | None:
        if path is None:
            return None
        file_row = file_by_path.get(path)
        if file_row is None:
            return None
        return root_entries.get(file_row["sync_root_id"])

    for review in store.find_open_reviews(conn, cause_kind="violation"):
        detail = _parse_cause_ref(review["cause_ref"])
        path = detail.get("path")
        item = {**review, "path": path}
        bucket_name = "pauses" if detail.get("pause") else "violations"
        target = _root_entry_for_path(path)
        if target is not None:
            target[bucket_name].append(item)
        else:
            unresolved.append({**item, "bucket": bucket_name})

    for review in store.find_open_reviews(conn, cause_kind="conflict"):
        detail = _parse_cause_ref(review["cause_ref"])
        path = detail.get("path")
        item = {**review, "path": path}
        target = _root_entry_for_path(path)
        if target is not None:
            target["conflicts"].append(item)
        else:
            unresolved.append({**item, "bucket": "conflicts"})

    return {"sync_roots": list(root_entries.values()), "unresolved": unresolved}


@router.post("/rescan")
def sync_rescan(
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Trigger a full §4.8 reconcile of every managed file; return a convergence summary.

    Builds one fresh ``Reconciler``/``OriginTracker`` pair per request — a
    manual rescan has no live filesystem watcher to share state with (T5.6
    wires the long-lived daemon watcher+reconciler pair separately), and
    §4.8's ``on_change`` is idempotent (verified crash-replay-safe by
    T5.5/T5.6), so a fresh tracker is safe: at worst it fails to suppress
    one of *this request's own* write-back echoes, which only matters to a
    live filesystem watcher, not to this synchronous HTTP call.

    A path whose file has since been removed from disk (tracked in
    ``sync_files`` but no longer present in the vault) is skipped rather
    than failing the whole rescan -- one stale entry must not 500 an
    otherwise-successful convergence of every other file.
    """
    reconciler = Reconciler(conn, OriginTracker())
    files = store.list_sync_files(conn)
    files_reconciled = 0
    files_missing = 0
    for f in files:
        try:
            reconciler.on_change(f["path"])
        except FileNotFoundError:
            files_missing += 1
            continue
        files_reconciled += 1

    reviews_open = len(store.find_open_reviews(conn))
    return {
        "files_reconciled": files_reconciled,
        "files_missing": files_missing,
        "reviews_open": reviews_open,
    }
