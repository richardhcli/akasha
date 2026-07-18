"""Soak test: RSS/CPU residency + zero-unhandled-exceptions (build-plan T9.5).

Spec §9 story 9 / M9 DoD: ``RSS < 150 MB`` throughout, ``idle CPU ~= 0%``,
zero unhandled exceptions. This is a plain script (``python
tests/battery/soak.py``), not a pytest module -- pytest's default discovery
(``test_*.py``/``*_test.py``) never collects it, so it cannot appear in
``make battery``'s run and does not need pytest fixtures. That mirrors the
build-plan's own literal ``Verify`` (``uv run python tests/battery/soak.py
--hours 24``), a direct interpreter invocation.

One code path, two invocations (build-plan Steps: "24 h, OR a scaled proxy
in CI with a full run nightly on `main`"):

* ``--hours 24`` (nightly, Windows CI, ``.github/workflows/ci.yml``'s
  ``nightly-soak`` job) -- the literal 24-hour run.
* ``--hours 0.05`` (~3 real minutes) -- the short, in-session proxy: the
  SAME tick loop, just fewer ticks, run to completion in one fleet-worker
  session.

``--hours`` is real wall-clock duration (``hours * 3600`` seconds); the tick
loop is driven through an injectable :class:`Clock` rather than a bare
``time.sleep`` call so the loop's timing is never hardcoded to the real
clock. Both invocations above use :class:`RealClock` (a soak test that
never actually waits proves nothing about real-time residency); the
injection point exists so the same ``run_soak`` loop could, in principle,
be driven by a :class:`FakeClock` for an instant structural exercise of the
loop's logic -- see that class's docstring.

Traffic is driven through the REAL production surfaces end to end, reusing
existing modules rather than reinventing them (rule 8 / this task's own
scope guard):

* ``kernel/store.py`` + ``sync/reconcile.py`` directly, to bootstrap each
  vault file's very first sighting (mirrors what the filesystem watcher
  would do -- there is no live watcher thread in this harness).
* The real FastAPI app (``api/app.py::create_app``) via ``TestClient``
  (in-process ASGI, no socket) for every node/edge/review/search/rescan
  action thereafter -- the actual ``/v1/*`` HTTP surface, request bodies
  and all, not a shortcut around it.
* ``akasha.metrics.compute_metrics`` (via ``GET /v1/metrics``) for every
  RSS/CPU sample -- the exact T9.2 sampling helpers, never reimplemented
  here (this task's explicit instruction).
* ``akasha.daemon.configure_logging`` for the JSON-line log this script
  scans for unhandled exceptions -- the exact T0.6/T9.3 logging helper,
  same reasoning.

Design note on "the daemon process": this harness runs the FastAPI app
in-process (``TestClient``, no subprocess, no real socket) rather than
spawning a real ``uvicorn`` daemon. ``metrics.py``'s RSS/CPU samplers read
``/proc/self`` (or the platform equivalent) -- i.e. "the current process" --
so sampling only means what it's supposed to mean (this daemon's own
residency) when harness and app share one process, which is exactly this
design. It also lets one script embed the two verbs (host process + HTTP
client) that a real deployment splits across a socket, without the
complexity of managing a second process's lifecycle/lock file/log
tailing/port allocation purely to prove the same code paths stay lightweight
under sustained traffic.

"Unhandled exception" detection: this app registers exception handlers only
for ``ApiError``/``RequestValidationError`` (``api/deps.py``) -- deliberately
out of this task's Files list, so it is not edited here. A genuine bug
elsewhere in the stack (not one of those two expected/typed error paths)
propagates out of a ``TestClient`` call as a raised Python exception
(``TestClient``'s default ``raise_server_exceptions=True``) rather than a
500 response. Each tick's action is run inside a ``try/except Exception``
that, on catch, logs via ``logger.exception(...)`` to the SAME JSON-line log
``configure_logging`` set up -- turning "a real bug surfaced during this
run" into exactly the durable, greppable JSON-log record the DoD asks this
script to assert zero of, and then treats it as a soak failure (fail-fast,
per build-plan rule 9 -- never continue past a real failure hoping the rest
of the run looks fine).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from fastapi.testclient import TestClient

from akasha import daemon
from akasha.api import auth
from akasha.api.app import create_app
from akasha.config import Config
from akasha.contract.parser import parse
from akasha.contract.render import render
from akasha.kernel import store
from akasha.kernel.ids import contract_anchor
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import Reconciler

RSS_LIMIT_BYTES_DEFAULT = 150 * 1024 * 1024  # spec §9 story 9 / M9 DoD: RSS < 150 MB.

# "idle CPU ~= 0%" (M9 DoD) names no exact number. Narrowest-reading judgment
# call (not a SPEC-QUESTION -- a numeric-threshold pick, same class of
# decision as tests/battery/test_edit_battery.py's E20 400MB memory cap):
# each tick samples once, right after its own (tiny) unit of work and BEFORE
# sleeping out the rest of the tick, so the delta window `_sample_idle_cpu_pct`
# measures is dominated by the PRIOR tick's idle sleep, not the current
# action -- a genuinely-idle daemon under light, sustained traffic should
# read a low single-digit percentage here. 30% gives real headroom above
# that for slow/contended CI runners while still catching a genuine
# runaway/busy-loop regression.
IDLE_CPU_THRESHOLD_PCT_DEFAULT = 30.0

DEFAULT_TICK_SECONDS = 2.0
DEFAULT_SEED = 1234
DEFAULT_HEARTBEAT_EVERY_N_TICKS = 30
VAULT_FILE_COUNT = 3
NODES_PER_VAULT_FILE = 2

_NODE_TYPES = ("claim", "entity", "evidence", "task")


class SoakFailure(RuntimeError):
    """Raised when the soak's own DoD assertions (RSS/CPU/exceptions) are violated."""


class Clock(Protocol):
    """Injectable time source for the tick loop (module docstring)."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class RealClock:
    """Production clock: genuine wall-clock time. Used by both CLI invocations."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """Instant, virtual-time clock (module docstring: the injection point's payoff).

    ``sleep`` never actually blocks; it only advances an internal virtual
    counter that ``monotonic`` reports. Not used by this script's CLI (a
    soak test that never waits proves nothing about real-time residency —
    see the module docstring), but ``run_soak`` accepting any ``Clock``
    (rather than calling ``time.sleep`` directly) means the exact same tick
    loop could be driven by this class for an instant, deterministic,
    zero-real-time structural exercise of the loop's logic elsewhere
    (e.g. an interactive smoke check), without duplicating the loop.
    """

    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._now += seconds


@dataclass
class SoakStats:
    ticks_completed: int = 0
    rss_samples: list[int] = field(default_factory=list)
    idle_cpu_samples: list[float] = field(default_factory=list)
    max_rss_bytes: int = 0
    mean_idle_cpu_pct: float = 0.0
    unhandled_exceptions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticks_completed": self.ticks_completed,
            "samples_taken": len(self.rss_samples),
            "max_rss_bytes": self.max_rss_bytes,
            "max_rss_mb": round(self.max_rss_bytes / (1024 * 1024), 2),
            "mean_idle_cpu_pct": round(self.mean_idle_cpu_pct, 3),
            "unhandled_exception_count": len(self.unhandled_exceptions),
        }


def _managed(body: str) -> str:
    return f"---\ntm: 1\n---\n{body}"


def _canonical_vault_text(node_ids: list[str], tick: int) -> str:
    lines = "".join(
        f"note body for {nid} @ tick {tick} {contract_anchor(nid)}\n" for nid in node_ids
    )
    return render(parse(_managed(lines)))


def _scan_log_for_errors(log_file: Path) -> list[str]:
    """Every ``ERROR``/``CRITICAL`` JSON line in ``log_file`` (DoD: "zero unhandled exceptions")."""
    if not log_file.exists():
        return []
    hits: list[str] = []
    for raw_line in log_file.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except ValueError:
            continue  # a non-JSON stray line is not this script's concern to parse
        if record.get("level") in ("ERROR", "CRITICAL"):
            hits.append(raw_line)
    return hits


@dataclass
class _SoakState:
    """Mutable state threaded through one soak run's tick actions."""

    conn: Any
    client: TestClient
    headers: dict[str, str]
    logger: logging.Logger
    rng: random.Random
    live_node_ids: list[str]
    vault_paths: list[Path]
    vault_node_ids: dict[Path, list[str]]
    sync_root_id: str
    reconciler: Reconciler
    tick: int = 0


def _setup(tmp_dir: Path, seed: int, logger: logging.Logger) -> _SoakState:
    db_path = tmp_dir / "store.db"
    conn = store.connect(db_path, check_same_thread=False)
    store.run_migrations(conn)

    raw_secret = auth.mint_secret()
    token = store.create_token(conn, "soak-human", "human", auth.hash_secret(raw_secret))
    bearer = auth.format_bearer_token(token["id"], raw_secret)
    headers = {"Authorization": f"Bearer {bearer}"}

    app = create_app(config=Config(), conn=conn)
    client = TestClient(app, raise_server_exceptions=True)

    vault_dir = tmp_dir / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    resp = client.post(
        "/v1/sync/roots",
        json={"name": "soak-vault", "root_path": str(vault_dir)},
        headers=headers,
    )
    resp.raise_for_status()
    sync_root_id = resp.json()["id"]

    live_node_ids: list[str] = []
    vault_paths: list[Path] = []
    vault_node_ids: dict[Path, list[str]] = {}
    reconciler = Reconciler(conn, OriginTracker())

    for i in range(VAULT_FILE_COUNT):
        seeded_ids: list[str] = []
        for j in range(NODES_PER_VAULT_FILE):
            node_type = _NODE_TYPES[(i * NODES_PER_VAULT_FILE + j) % len(_NODE_TYPES)]
            resp = client.post(
                "/v1/nodes",
                json={"node_type": node_type, "body": f"soak seed node {i}.{j}"},
                headers=headers,
            )
            resp.raise_for_status()
            node_id = resp.json()["id"]
            seeded_ids.append(node_id)
            live_node_ids.append(node_id)

        path = vault_dir / f"note-{i}.md"
        path.write_text(_canonical_vault_text(seeded_ids, tick=0), encoding="utf-8")
        # Bootstrap this file's very first sighting directly through the real
        # reconcile pipeline (module docstring: mirrors what a live
        # filesystem watcher would do -- there is no watcher thread here).
        reconciler.on_change(str(path))
        vault_paths.append(path)
        vault_node_ids[path] = seeded_ids

    logger.info(
        f"soak setup complete: {len(live_node_ids)} seed nodes, "
        f"{len(vault_paths)} vault files, sync_root={sync_root_id}"
    )

    return _SoakState(
        conn=conn,
        client=client,
        headers=headers,
        logger=logger,
        rng=random.Random(seed),
        live_node_ids=live_node_ids,
        vault_paths=vault_paths,
        vault_node_ids=vault_node_ids,
        sync_root_id=sync_root_id,
        reconciler=reconciler,
    )


# --- tick actions: each drives one realistic unit of edit traffic through
# the real store/reconcile/API surface (module docstring). -------------------


def _action_create_node(state: _SoakState) -> None:
    node_type = state.rng.choice(_NODE_TYPES)
    resp = state.client.post(
        "/v1/nodes",
        json={"node_type": node_type, "body": f"soak-created node at tick {state.tick}"},
        headers=state.headers,
    )
    if resp.status_code == 201:
        state.live_node_ids.append(resp.json()["id"])


def _action_patch_node(state: _SoakState) -> None:
    if not state.live_node_ids:
        return
    node_id = state.rng.choice(state.live_node_ids)
    change_class = state.rng.choice(("patch", "minor"))
    state.client.patch(
        f"/v1/nodes/{node_id}",
        json={
            "body": f"soak-edited body at tick {state.tick}",
            "change_class": change_class,
            "facets_touched": [],
        },
        headers=state.headers,
    )


def _action_create_edge(state: _SoakState) -> None:
    if len(state.live_node_ids) < 2:
        return
    src, dst = state.rng.sample(state.live_node_ids, 2)
    state.client.post(
        "/v1/edges",
        json={
            "src": src,
            "dst": dst,
            "edge_type": "cites",
            "facet_binding": "*",
            "provenance": "human",
        },
        headers=state.headers,
    )


def _action_vault_edit(state: _SoakState) -> None:
    path = state.rng.choice(state.vault_paths)
    node_ids = state.vault_node_ids[path]
    path.write_text(_canonical_vault_text(node_ids, tick=state.tick), encoding="utf-8")
    # Every subsequent edit to an already-known file goes through the real
    # `/v1/sync/rescan` HTTP surface (module docstring) -- only the very
    # first sighting of a file (in `_setup`) bypasses it.
    resp = state.client.post("/v1/sync/rescan", headers=state.headers)
    resp.raise_for_status()


def _action_search(state: _SoakState) -> None:
    state.client.get("/v1/search", params={"q": "soak"}, headers=state.headers)


def _action_review_cycle(state: _SoakState) -> None:
    resp = state.client.get("/v1/review", headers=state.headers)
    resp.raise_for_status()
    reviews = resp.json()["reviews"]
    if not reviews:
        return
    review = state.rng.choice(reviews)
    # "dismissed" may legitimately 409 for some cause_kinds (DismissalNotAllowedError)
    # -- an ordinary, typed 4xx response, never a raised exception (module
    # docstring); nothing further to do either way.
    state.client.post(
        f"/v1/review/{review['id']}/resolve",
        json={"resolution": "dismissed"},
        headers=state.headers,
    )


def _action_delete_node(state: _SoakState) -> None:
    if len(state.live_node_ids) < 4:
        return  # keep a floor of live nodes so other actions always have material
    node_id = state.rng.choice(state.live_node_ids)
    # httpx's `Client.delete()` convenience method does not forward a `json`
    # body (DELETE-with-body is atypical HTTP, but this route legitimately
    # accepts one -- `DeleteNodeBody`); `.request(...)` is the one httpx
    # entry point that does pass it through.
    resp = state.client.request(
        "DELETE", f"/v1/nodes/{node_id}", json={"tombstone": True}, headers=state.headers
    )
    if resp.status_code == 200:
        state.live_node_ids.remove(node_id)


# Weighted so the corpus stays roughly bounded over a long (24h) run rather
# than growing without limit: edits dominate over creates, deletes are rare.
_ACTIONS: list[tuple[str, Any, int]] = [
    ("create_node", _action_create_node, 2),
    ("patch_node", _action_patch_node, 6),
    ("create_edge", _action_create_edge, 2),
    ("vault_edit", _action_vault_edit, 3),
    ("search", _action_search, 2),
    ("review_cycle", _action_review_cycle, 2),
    ("delete_node", _action_delete_node, 1),
]
_ACTION_NAMES = [name for name, _, _ in _ACTIONS]
_ACTION_FUNCS = {name: func for name, func, _ in _ACTIONS}
_ACTION_WEIGHTS = [weight for _, _, weight in _ACTIONS]


def run_soak(
    *,
    hours: float,
    tick_seconds: float = DEFAULT_TICK_SECONDS,
    rss_limit_bytes: int = RSS_LIMIT_BYTES_DEFAULT,
    idle_cpu_threshold_pct: float = IDLE_CPU_THRESHOLD_PCT_DEFAULT,
    seed: int = DEFAULT_SEED,
    log_file: Path,
    heartbeat_every_n_ticks: int = DEFAULT_HEARTBEAT_EVERY_N_TICKS,
    clock: Clock | None = None,
) -> SoakStats:
    """Run one soak: realistic traffic + RSS/CPU sampling, for ``hours`` (real time).

    Raises :class:`SoakFailure` (fail-fast, build-plan rule 9) the moment
    any DoD assertion is violated: an RSS sample at/above ``rss_limit_bytes``,
    an unhandled exception surfacing (immediately, via the JSON log scan),
    or -- checked once, at the end, since it is a THROUGHOUT-the-run
    average rather than a per-sample bound -- the mean idle-CPU sample
    exceeding ``idle_cpu_threshold_pct``.
    """
    clock = clock if clock is not None else RealClock()
    logger = daemon.configure_logging(log_file, level=logging.INFO)
    total_ticks = max(1, round((hours * 3600.0) / tick_seconds))
    stats = SoakStats()

    logger.info(
        f"soak starting: hours={hours} tick_seconds={tick_seconds} total_ticks={total_ticks} "
        f"rss_limit_bytes={rss_limit_bytes} idle_cpu_threshold_pct={idle_cpu_threshold_pct}"
    )

    with tempfile.TemporaryDirectory(prefix="akasha-soak-") as tmp_dir_name:
        state = _setup(Path(tmp_dir_name), seed, logger)
        try:
            for tick in range(total_ticks):
                state.tick = tick
                tick_started_at = clock.monotonic()

                action_name = state.rng.choices(_ACTION_NAMES, weights=_ACTION_WEIGHTS, k=1)[0]
                try:
                    _ACTION_FUNCS[action_name](state)
                except Exception:
                    logger.exception(
                        f"soak tick {tick}: unhandled exception in action {action_name!r}"
                    )

                try:
                    metrics_resp = state.client.get("/v1/metrics", headers=state.headers)
                    metrics_resp.raise_for_status()
                    snapshot = metrics_resp.json()
                except Exception:
                    logger.exception(f"soak tick {tick}: unhandled exception sampling /v1/metrics")
                    snapshot = None

                if snapshot is not None:
                    rss_bytes = int(snapshot["rss_bytes"])
                    idle_cpu_pct = float(snapshot["idle_cpu_pct"])
                    stats.rss_samples.append(rss_bytes)
                    stats.idle_cpu_samples.append(idle_cpu_pct)
                    stats.max_rss_bytes = max(stats.max_rss_bytes, rss_bytes)
                    if rss_bytes >= rss_limit_bytes:
                        logger.error(
                            f"soak tick {tick}: RSS breach {rss_bytes} bytes "
                            f">= limit {rss_limit_bytes} bytes"
                        )
                        raise SoakFailure(
                            f"RSS budget breached at tick {tick}: {rss_bytes} bytes "
                            f">= {rss_limit_bytes} byte limit (spec §9 story 9 / M9 DoD)"
                        )

                error_lines = _scan_log_for_errors(log_file)
                if error_lines:
                    stats.unhandled_exceptions = error_lines
                    raise SoakFailure(
                        f"unhandled exception(s) detected in soak log at tick {tick} "
                        f"(see {log_file}): {error_lines[-1]}"
                    )

                stats.ticks_completed = tick + 1
                if heartbeat_every_n_ticks > 0 and (tick + 1) % heartbeat_every_n_ticks == 0:
                    last_rss = stats.rss_samples[-1] if stats.rss_samples else "n/a"
                    last_cpu = stats.idle_cpu_samples[-1] if stats.idle_cpu_samples else "n/a"
                    logger.info(
                        f"soak heartbeat: tick={tick + 1}/{total_ticks} "
                        f"rss_bytes={last_rss} idle_cpu_pct={last_cpu}"
                    )

                elapsed = clock.monotonic() - tick_started_at
                clock.sleep(max(0.0, tick_seconds - elapsed))
        finally:
            # Close the sqlite connection BEFORE the TemporaryDirectory
            # context exits and tries to remove the underlying files --
            # Windows (this task's own nightly-CI target, T9.1) locks open
            # file handles, so an unclosed connection would otherwise fail
            # the directory's own teardown, not just leak a handle.
            state.conn.close()

    # The first idle-CPU sample has no prior sample to diff against
    # (`metrics._sample_idle_cpu_pct`'s documented "no data yet" -> 0.0);
    # excluding it avoids deflating the mean with a sample that isn't a
    # real measurement.
    measured_cpu_samples = stats.idle_cpu_samples[1:] if len(stats.idle_cpu_samples) > 1 else []
    stats.mean_idle_cpu_pct = (
        sum(measured_cpu_samples) / len(measured_cpu_samples) if measured_cpu_samples else 0.0
    )

    logger.info(f"soak complete: {json.dumps(stats.as_dict())}")

    if measured_cpu_samples and stats.mean_idle_cpu_pct >= idle_cpu_threshold_pct:
        raise SoakFailure(
            f"mean idle CPU {stats.mean_idle_cpu_pct:.2f}% >= "
            f"{idle_cpu_threshold_pct}% threshold (spec §9 story 9 / M9 DoD: idle CPU ~= 0%)"
        )

    final_errors = _scan_log_for_errors(log_file)
    if final_errors:
        stats.unhandled_exceptions = final_errors
        raise SoakFailure(
            f"unhandled exception(s) detected in final soak log scan: {final_errors[-1]}"
        )

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="Real wall-clock duration of the soak, in hours (default: 24.0, the literal "
        "build-plan Verify). Pass a small value (e.g. 0.05 = 3 minutes) for an in-session proxy.",
    )
    parser.add_argument("--tick-seconds", type=float, default=DEFAULT_TICK_SECONDS)
    default_rss_limit_mb = RSS_LIMIT_BYTES_DEFAULT / (1024 * 1024)
    parser.add_argument("--rss-limit-mb", type=float, default=default_rss_limit_mb)
    parser.add_argument(
        "--idle-cpu-threshold-pct", type=float, default=IDLE_CPU_THRESHOLD_PCT_DEFAULT
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="JSON-line log path (default: a fresh temp file, printed on completion).",
    )
    parser.add_argument(
        "--heartbeat-every-n-ticks", type=int, default=DEFAULT_HEARTBEAT_EVERY_N_TICKS
    )
    args = parser.parse_args(argv)

    log_file = args.log_file
    if log_file is None:
        fd, name = tempfile.mkstemp(prefix="akasha-soak-", suffix=".jsonl")
        os.close(fd)
        log_file = Path(name)

    try:
        stats = run_soak(
            hours=args.hours,
            tick_seconds=args.tick_seconds,
            rss_limit_bytes=int(args.rss_limit_mb * 1024 * 1024),
            idle_cpu_threshold_pct=args.idle_cpu_threshold_pct,
            seed=args.seed,
            log_file=log_file,
            heartbeat_every_n_ticks=args.heartbeat_every_n_ticks,
        )
    except SoakFailure as exc:
        print(f"SOAK FAILED: {exc}", file=sys.stderr)
        print(f"log file: {log_file}", file=sys.stderr)
        return 1

    print(json.dumps({"status": "passed", "log_file": str(log_file), **stats.as_dict()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
