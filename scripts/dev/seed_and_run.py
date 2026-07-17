#!/usr/bin/env python3
"""Dev-only harness: seed a throwaway akasha graph and serve it for manual UI testing.

NOT a build-plan task (see the "ITEM-A" dev-tooling entry in
docs/agents/fleet-architecture.md). Every M8 Web-UI browser-DoD check
(T8.2-T8.5) needs the same rich node state -- in particular a stale badge,
which only renders when a node has an OPEN ``facet_break`` review -- so this
script builds that state once and serves it so a human (or agent) can drive
the Web UI in a browser.

Total isolation: creates a fresh ``tempfile.mkdtemp()`` directory and puts
the database at ``<tmpdir>/store.db``. This NEVER touches the user's real
store (``akasha.config.default_db_path()``) and never goes through the CLI
``daemon`` verb / ``akasha.daemon.serve`` -- those acquire a single-instance
lock on the default config dir and would collide with a real daemon. Instead
this serves ``akasha.api.app.create_app`` directly via uvicorn.

Rule 0.4 (all persistent writes go through ``kernel/store.py``): this script
issues NO raw SQL; every write is a call to a ``store.*`` function
(``create_node``, ``commit_node``, ``create_edge``, ``enqueue_review``,
``create_token``). Rule 0.5 (no pickle/eval/exec): none used here.

Usage:
    uv run python scripts/dev/seed_and_run.py [--port PORT] [--seed-only]

``--seed-only`` seeds the graph, asserts the fixture invariants, prints the
summary, and exits 0 WITHOUT starting uvicorn (must not block) -- this is
the non-hanging path used by ``make dev-ui``'s verify step and CI. Without
the flag, the script also starts uvicorn (blocking) so a human can open the
printed URL in a browser; the throwaway tempdir is removed on shutdown.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import sys
import tempfile
from pathlib import Path

from akasha.api import auth
from akasha.config import Config
from akasha.kernel import ids, store
from akasha.kernel.model import Facet

# Free text deliberately includes an XSS canary, an ampersand, and a quoted
# clause so T8.2's browser check can exercise textContent escaping in the
# node-view UI (the DoD requires the raw markup to render as inert text, not
# execute).
INITIAL_BODY = (
    'The Earth orbits the Sun. <img src=x onerror="alert(1)"> & a "quoted" clause.'
)
REVISED_BODY = (
    'The Earth orbits the Sun once per year. <img src=x onerror="alert(2)"> '
    '& a "revised quoted" clause.'
)


def _free_port() -> int:
    """Bind an ephemeral port on 127.0.0.1 and return its number (then release it)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def seed(db_path: Path) -> dict[str, str]:
    """Seed a fresh graph into db_path via kernel/store.py only. Returns a summary dict."""
    conn = store.connect(db_path)
    store.run_migrations(conn)

    facet_1 = Facet(facet_id=ids.mint(), name="orbit-claim", span="orbits the Sun", version=1)
    facet_2 = Facet(
        facet_id=ids.mint(), name="annual-period", span="once per year", version=1
    )

    node_a = store.create_node(
        conn,
        node_type="claim",
        body=INITIAL_BODY,
        facets=[facet_1, facet_2],
        author="human",
        message="seed: initial",
    )
    store.commit_node(
        conn,
        node_a.id,
        new_body=REVISED_BODY,
        change_class="minor",
        facets_touched=[],
        author="human",
        message="seed: revision",
    )

    node_b = store.create_node(
        conn,
        node_type="evidence",
        body="Observational parallax data confirms heliocentric orbit.",
        author="human",
        message="seed: node B",
    )
    node_c = store.create_node(
        conn,
        node_type="claim",
        body="Heliocentrism is the scientific consensus.",
        author="human",
        message="seed: node C",
    )

    # A -> B: composes (facet_binding None is legal for composes/redirects_to).
    store.create_edge(
        conn,
        src=node_a.id,
        dst=node_b.id,
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )
    # C -> A: supports, a justification edge type, bound to one of A's facets.
    store.create_edge(
        conn,
        src=node_c.id,
        dst=node_a.id,
        edge_type="supports",
        facet_binding=facet_1.facet_id,
        provenance="human",
    )

    review = store.enqueue_review(conn, node_a.id, "facet_break", facet=facet_1.facet_id)

    raw_secret = auth.mint_secret()
    secret_hash = auth.hash_secret(raw_secret)
    token = store.create_token(conn, name="dev", token_class="human", secret_hash=secret_hash)
    bearer = auth.format_bearer_token(token["id"], raw_secret)

    conn.close()

    return {
        "focus_node_id": node_a.id,
        "node_b_id": node_b.id,
        "node_c_id": node_c.id,
        "review_id": review["id"],
        "bearer": bearer,
    }


def assert_invariants(db_path: Path, summary: dict[str, str]) -> None:
    """Fail loudly (AssertionError) if any dev-fixture invariant does not hold."""
    conn = store.connect(db_path)
    try:
        node_a_id = summary["focus_node_id"]

        node = store.get_node(conn, node_a_id)
        assert len(node.facets) == 2, (
            f"expected exactly 2 facets on {node_a_id}, got {len(node.facets)}"
        )

        hist = store.history(conn, node_a_id)
        assert len(hist) >= 2, f"expected >=2 history entries on {node_a_id}, got {len(hist)}"

        nbhd = store.neighborhood(conn, node_a_id, hops=1)
        neighbors = set(nbhd["node_ids"]) - {node_a_id}
        assert len(neighbors) >= 2, (
            f"expected >=2 distinct neighbors of {node_a_id}, got {neighbors}"
        )

        open_reviews = store.find_open_reviews(
            conn, node_id=node_a_id, cause_kind="facet_break"
        )
        assert len(open_reviews) >= 1, (
            f"expected >=1 open facet_break review on {node_a_id}, got {len(open_reviews)}"
        )

        ctx = auth.authenticate(conn, summary["bearer"])
        assert ctx.token_class == "human", (
            f"expected minted bearer token_class 'human', got {ctx.token_class!r}"
        )
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to serve on (default: a free ephemeral port)",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Seed, assert invariants, print summary, exit 0 -- do NOT start uvicorn",
    )
    args = parser.parse_args()

    tmpdir = Path(tempfile.mkdtemp(prefix="akasha-dev-ui-"))
    db_path = tmpdir / "store.db"

    try:
        summary = seed(db_path)
        assert_invariants(db_path, summary)

        port = args.port if args.port is not None else _free_port()
        base_url = f"http://127.0.0.1:{port}/"

        print("=== akasha dev-ui seed summary ===")
        print(f"Throwaway DB path:           {db_path}")
        print(f"Base URL:                    {base_url}")
        print(f"Bearer token:                {summary['bearer']}")
        print(f"Focus node id:               {summary['focus_node_id']}")
        print(f"Neighbor node B id:          {summary['node_b_id']}")
        print(f"Neighbor node C id:          {summary['node_c_id']}")
        print(f"Open facet_break review id:  {summary['review_id']}")
        print("All seed invariants passed.")
        print("===================================")

        if args.seed_only:
            print("--seed-only: exiting without serving.")
            return 0

        import uvicorn

        from akasha.api.app import create_app

        cfg = Config(db_path=db_path, port=port, bind="127.0.0.1")
        app = create_app(cfg)
        print(f"Serving on {base_url} (Ctrl-C to stop and clean up)")
        try:
            uvicorn.run(app, host="127.0.0.1", port=port)
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
