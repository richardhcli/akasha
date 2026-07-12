"""Integration performance benchmark for ``store.neighborhood`` (spec §4.11 note, M1 DoD).

Seeds a 10,000-node / modest-out-degree synthetic graph via the store API
(build-plan rule 0.4: all writes go through ``store.py`` — never raw
``INSERT``s), then measures the p95 wall-clock latency of
``neighborhood(conn, node_id, hops=1)`` over a large random sample of node
ids. Seeding the graph is setup and is intentionally NOT timed; only the
``neighborhood`` calls themselves are measured.
"""

from __future__ import annotations

import random
import statistics
import tempfile
import time
from pathlib import Path

from akasha.kernel import store

_N_NODES = 10_000
_AVG_OUT_DEGREE = 3
_N_SAMPLES = 500
_P95_BUDGET_SECONDS = 0.050


def test_neighborhood_p95() -> None:
    rng = random.Random(1234)

    with tempfile.TemporaryDirectory() as tmp:
        conn = store.connect(Path(tmp) / "perf.sqlite3")
        store.run_migrations(conn)

        # --- Setup: seed a 10,000-node graph via the store API (NOT timed) ---
        node_ids: list[str] = []
        for _ in range(_N_NODES):
            node = store.create_node(
                conn, "claim", "perf-seed node body", author="perf-bench"
            )
            node_ids.append(node.id)

        n_edges = _N_NODES * _AVG_OUT_DEGREE
        for _ in range(n_edges):
            src = rng.choice(node_ids)
            dst = rng.choice(node_ids)
            if src == dst:
                continue
            store.create_edge(
                conn,
                src=src,
                dst=dst,
                edge_type="composes",
                facet_binding=None,
                provenance="human",
            )

        # --- Measurement: time neighborhood(hops=1) over a random sample ---
        sample_ids = [rng.choice(node_ids) for _ in range(_N_SAMPLES)]
        latencies: list[float] = []
        for node_id in sample_ids:
            start = time.perf_counter()
            store.neighborhood(conn, node_id, hops=1)
            latencies.append(time.perf_counter() - start)

        latencies.sort()
        p95 = statistics.quantiles(latencies, n=100)[94]

        assert p95 < _P95_BUDGET_SECONDS, (
            f"p95 neighborhood(hops=1) latency over {_N_SAMPLES} samples on a "
            f"{_N_NODES}-node / {n_edges}-edge graph was {p95 * 1000:.3f} ms, "
            f"expected < {_P95_BUDGET_SECONDS * 1000:.0f} ms"
        )
