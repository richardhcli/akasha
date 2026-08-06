"""Reconcile pipeline tests (task T5.4, spec §4.8; the M5 T3.5/T3.6 follow-up on
cross-file ``E_DUP_ID`` in ``docs/agents/task-status.md``).

Two layers, matching ``reconcile.py``'s own split:

- Pure ``diff_blocks``/``_compute_ops`` table tests (zero-I/O): one per
  §4.8 op kind (modified/checkbox_toggled/created/deleted/moved/
  reparented), plus violation-withholding and the cross-file E04/E05
  classification (spec §7).
- ``Reconciler`` integration tests against a real (in-memory) sqlite store
  and real temp-directory files: quiet/hub-only shortcuts, conflict
  skip+enqueue+vault-version-in-cause_ref, convergent no-op, pause makes
  zero writes, ``^tm-new`` mint+rewrite+origin-recorded, echo recording on
  write-back, ``base_store.put`` called with H2.

Plus a golden-fixture loader driving the 6 seeded cases under
``tests/golden/reconcile/<case>/``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from akasha import metrics
from akasha.contract.parser import parse
from akasha.contract.render import render
from akasha.kernel import ids, store
from akasha.kernel.canonical import canonicalize_text, object_hash
from akasha.kernel.ids import contract_anchor
from akasha.sync import base_store, reconcile
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import ProjectionIndex, Reconciler, diff_blocks

GOLDEN_ROOT = Path(__file__).resolve().parents[2] / "golden" / "reconcile"


@pytest.fixture(autouse=True)
def _reset_metrics_recorder():
    """T9.2c: ``metrics._recorder`` is a module-level singleton (spec §7);

    isolate every test in this file from cycle/repair counts left over by
    a previous test (and from leaking its own counts into a later one),
    the same isolation ``tests/unit/test_metrics.py`` already applies.
    """
    metrics.reset_recorder()
    yield
    metrics.reset_recorder()


# --- shared test helpers --------------------------------------------------------


def _conn() -> sqlite3.Connection:
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return conn


def _seed_node(
    conn,
    node_id: str,
    node_type: str,
    body: str,
    task_state: str | None = None,
) -> None:
    """Test-only fixture seeding: insert a node under a CHOSEN id.

    ``store.create_node`` always mints a fresh id (spec §4.1) -- golden
    fixtures need deterministic, pre-chosen ids to match their committed
    ``base.md``/``vault.md`` anchors. Mirrors ``store._create_node_tx``'s
    exact sequence (object insert, ``nodes`` row, ``nodes_fts`` row,
    genesis commit) using ``store``'s own private helpers rather than
    reimplementing them -- this is test fixture plumbing, not a second
    production write path.
    """
    now = store._now()
    canonical_body = canonicalize_text(body)
    content = store._node_content(canonical_body, [], task_state)
    with conn:
        obj_hash = store._insert_object(conn, content, now)
        conn.execute(
            "INSERT INTO nodes (id, node_type, head_hash, maturity, status, vetted, "
            "created_at, updated_at) VALUES (?, ?, ?, 'S0', 'live', 0, ?, ?)",
            (node_id, node_type, obj_hash, now, now),
        )
        conn.execute("INSERT INTO nodes_fts (id, body) VALUES (?, ?)", (node_id, canonical_body))
        store._insert_commit(
            conn,
            node_id,
            parents=[],
            object_hash_=obj_hash,
            change_class="major",
            facets_touched=[],
            author="test",
            message="",
            now=now,
        )


def _managed(body: str) -> str:
    return canonicalize_text(f"---\ntm: 1\n---\n{body}")


def _register_root(conn, root_path: Path) -> str:
    return store.register_sync_root(conn, "vault", str(root_path))["id"]


def _maturity_map(mapping: dict[str, str]):
    def lookup(node_id: str) -> str | None:
        return mapping.get(node_id)

    return lookup


# =================================================================================
# Pure diff_blocks / _compute_ops table tests (zero-I/O)
# =================================================================================


def test_diff_blocks_detects_modified():
    x = "23zl56h5"
    base = parse(_managed(f"Original text {contract_anchor(x)}\n"))
    vault = parse(_managed(f"Updated text {contract_anchor(x)}\n"))
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=_managed(f"Updated text {contract_anchor(x)}\n"),
        maturity=_maturity_map({x: "S0"}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert [(op.kind, op.node_id) for op in outcome.ops] == [("modified", x)]


def test_diff_blocks_detects_checkbox_toggled():
    x = "24qmgnvr"
    base = parse(_managed(f"- [ ] Buy milk {contract_anchor(x)}\n"))
    vault_text = _managed(f"- [x] Buy milk {contract_anchor(x)}\n")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({x: "S0"}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert [(op.kind, op.node_id) for op in outcome.ops] == [("checkbox_toggled", x)]


def test_diff_blocks_detects_created_tm_new():
    base = parse(_managed(""))
    vault_text = _managed("A brand new idea ^tm-new\n")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert len(outcome.ops) == 1
    op = outcome.ops[0]
    assert op.kind == "created"
    assert op.node_id is None
    assert op.new_request is not None
    assert op.new_request.text == "A brand new idea"


def test_diff_blocks_detects_deleted_s0():
    x = "2ha7cfbt"
    base = parse(_managed(f"Temporary note {contract_anchor(x)}\n"))
    vault_text = _managed("")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({x: "S0"}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert [(op.kind, op.node_id) for op in outcome.ops] == [("deleted", x)]


def test_diff_blocks_detects_moved():
    a, b = "4cgfdxpi", "5hqlvwua"
    base = parse(
        _managed(
            f"- [ ] Task Alpha {contract_anchor(a)}\n"
            f"- [ ] Task Beta {contract_anchor(b)}\n"
            f"- [ ] Task Gamma {contract_anchor('6p5zkk6x')}\n"
        )
    )
    # Rotate left: [A,B,C] -> [B,C,A]. The unique LCS is [B,C]; A moves.
    vault_text = _managed(
        f"- [ ] Task Beta {contract_anchor(b)}\n"
        f"- [ ] Task Gamma {contract_anchor('6p5zkk6x')}\n"
        f"- [ ] Task Alpha {contract_anchor(a)}\n"
    )
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({a: "S0", b: "S0", "6p5zkk6x": "S0"}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert [(op.kind, op.node_id) for op in outcome.ops] == [("moved", a)]


def test_diff_blocks_detects_reparented():
    p1, p2, c = "6pedsu6y", "aglyj3hn", "b5fl32uf"
    base_text = _managed(
        f"- [ ] Parent One {contract_anchor(p1)}\n"
        f"  - [ ] Child {contract_anchor(c)}\n"
        f"- [ ] Parent Two {contract_anchor(p2)}\n"
    )
    base = parse(base_text)
    vault_text = _managed(
        f"- [ ] Parent One {contract_anchor(p1)}\n"
        f"- [ ] Parent Two {contract_anchor(p2)}\n"
        f"  - [ ] Child {contract_anchor(c)}\n"
    )
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text=base_text,
        vault_text=vault_text,
        maturity=_maturity_map({p1: "S0", p2: "S0", c: "S0"}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert [(op.kind, op.node_id) for op in outcome.ops] == [("reparented", c)]
    op = outcome.ops[0]
    assert op.base_block is not None and op.base_block.parent_id == p1
    assert op.vault_block is not None and op.vault_block.parent_id == p2


def test_diff_blocks_reparented_can_co_occur_with_modified():
    p1, p2, c = "bctijda5", "bo6hnnht", "cepsm3ny"
    base_text = _managed(
        f"- [ ] Parent One {contract_anchor(p1)}\n"
        f"  - [ ] old child text {contract_anchor(c)}\n"
        f"- [ ] Parent Two {contract_anchor(p2)}\n"
    )
    base = parse(base_text)
    vault_text = _managed(
        f"- [ ] Parent One {contract_anchor(p1)}\n"
        f"- [ ] Parent Two {contract_anchor(p2)}\n"
        f"  - [ ] new child text {contract_anchor(c)}\n"
    )
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text=base_text,
        vault_text=vault_text,
        maturity=_maturity_map({p1: "S0", p2: "S0", c: "S0"}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    kinds = {(op.kind, op.node_id) for op in outcome.ops}
    assert ("modified", c) in kinds
    assert ("reparented", c) in kinds
    assert len(outcome.ops) == 2


# --- _stable_order_ids oracle (T5.8-1: O(n^2) LCS -> O(n log n) LIS perf fix) ----


def _lcs_oracle(a: list[str], b: list[str]) -> set[str]:
    """The RETIRED ``reconcile._lcs_ids`` DP, kept here verbatim as the oracle.

    ``reconcile._stable_order_ids`` (build-plan T5.8-1, fable-reviewed,
    human-decided 2026-07-12) replaced this O(n*m) longest-common-
    subsequence DP with an O(n log n) LIS-based algorithm to fix the E20
    5,000-block reconcile perf bug (~15s/cycle vs spec §6.2's <2s budget).
    This function is the untouched, original DP -- the correctness
    contract ``_stable_order_ids`` must reproduce EXACTLY, including its
    left-to-right tie-break (each id matched at its EARLIEST possible
    ``v_order`` position), since the "moved" op set it drives is pinned
    byte-identical by golden fixtures (E03/E17).
    """
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return set()
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row_i = dp[i]
        row_i1 = dp[i + 1]
        for j in range(m - 1, -1, -1):
            if a[i] == b[j]:
                row_i[j] = row_i1[j + 1] + 1
            else:
                row_i[j] = max(row_i1[j], row_i[j + 1])
    i = j = 0
    result: set[str] = set()
    while i < n and j < m:
        if a[i] == b[j]:
            result.add(a[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return result


@given(st.permutations(list(range(60))), st.permutations(list(range(60))))
@settings(max_examples=200)
def test_stable_order_ids_matches_lcs_oracle(b_perm, v_perm):
    """Hypothesis property: ``_stable_order_ids`` == the old LCS DP, always.

    Random independent permutations of the SAME id set (``b_order``,
    ``v_order`` -- exactly the invariant ``_compute_ops`` relies on:
    ``stable_ids ⊆ common``, both filtered orders are permutations of one
    another) -- a divergence here would mean the "moved" ops the reconcile
    pipeline emits silently changed, which the E03/E17 goldens forbid.
    """
    b_order = [f"id{i}" for i in b_perm]
    v_order = [f"id{i}" for i in v_perm]
    assert reconcile._stable_order_ids(b_order, v_order) == _lcs_oracle(b_order, v_order)


@given(st.permutations(list(range(20))))
@settings(max_examples=50)
def test_stable_order_ids_identical_order_is_fully_stable(perm):
    """Fast-path property: an unchanged order is entirely stable (the E20 case).

    ``b_order == v_order`` must return every id as stable (zero ``moved``
    ops) -- both via the O(n) fast path AND matching what the oracle DP
    would independently conclude for two identical sequences.
    """
    order = [f"id{i}" for i in perm]
    assert reconcile._stable_order_ids(order, order) == set(order)
    assert reconcile._stable_order_ids(order, order) == _lcs_oracle(order, order)


# --- violation withholding ------------------------------------------------------


def test_e_deleted_s1_id_withheld_from_ops():
    x = "dedgoghh"
    base = parse(_managed(f"Important claim {contract_anchor(x)}\n"))
    vault_text = _managed("totally unrelated other content\n")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({x: "S1"}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert all(op.node_id != x for op in outcome.ops)
    assert any(item.code == "E_DELETED_S1" and item.id == x for item in outcome.lint.review_items)


def test_e_id_checksum_withheld_from_ops():
    # A well-shaped but checksum-invalid anchor: never an op, always review.
    bad_id = "aaaaaaab"  # shape-valid (8 lowercase base32 chars), checksum wrong
    base = parse(_managed(""))
    vault_text = _managed(f"Some text {contract_anchor(bad_id)}\n")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert all(op.node_id != bad_id for op in outcome.ops)
    checksum_items = [item for item in outcome.lint.review_items if item.code == "E_ID_CHECKSUM"]
    assert any(item.id == bad_id for item in checksum_items)


# --- cross-file classification (spec §7) ----------------------------------------


def test_cross_file_move_adopts_when_unowned():
    x = "dyo6vafb"
    base = parse(_managed(""))
    vault_text = _managed(f"Adopted text {contract_anchor(x)}\n")
    vault = parse(vault_text)
    # Unowned anywhere -- e.g. the source file already vacated it in its own
    # prior cycle (ProjectionIndex reflects that by having no owner at all).
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({x: "S0"}),
        projection=ProjectionIndex(),
        current_path="f.md",
    )
    assert [(op.kind, op.node_id) for op in outcome.ops] == [("created", x)]
    assert outcome.extra_review_items == []


def test_cross_file_dup_withholds_and_reviews():
    x = "fcc6mpfa"
    projection = ProjectionIndex()
    projection.update("other.md", {x})
    base = parse(_managed(""))
    vault_text = _managed(f"Copied text {contract_anchor(x)}\n")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({x: "S0"}),
        projection=projection,
        current_path="f.md",
    )
    assert all(op.node_id != x for op in outcome.ops)
    assert len(outcome.extra_review_items) == 1
    item = outcome.extra_review_items[0]
    assert item.code == "E_DUP_ID"
    assert item.id == x


def test_unknown_anchor_id_is_withheld_and_reviewed():
    x = "fr5wvmjg"  # a real, checksum-valid id -- but never minted anywhere
    base = parse(_managed(""))
    vault_text = _managed(f"References an id that does not exist {contract_anchor(x)}\n")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({}),  # empty -> unknown
        projection=ProjectionIndex(),
        current_path="f.md",
    )
    assert all(op.node_id != x for op in outcome.ops)
    assert len(outcome.extra_review_items) == 1
    assert outcome.extra_review_items[0].code == "E_UNKNOWN_ANCHOR"


# --- anchor_elsewhere (T5.8-3: S0 cross-file move withholding) ------------------


def test_compute_ops_withholds_delete_when_anchor_elsewhere_finds_a_match():
    """A fake ``anchor_elsewhere`` returning a path withholds the ``deleted`` op.

    Mirrors ``Reconciler``'s real scan's contract: given a base-only id
    about to be hard-deleted, ``anchor_elsewhere(id)`` returning some OTHER
    path is proof of a move-in-flight -- ``_compute_ops`` must not emit a
    ``deleted`` op for it.
    """
    x = "sy5phfxx"
    base = parse(_managed(f"Shared text {contract_anchor(x)}\n"))
    vault_text = _managed("")  # source side: the anchor was cut out
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({x: "S0"}),
        projection=ProjectionIndex(),
        current_path="source.md",
        anchor_elsewhere=lambda node_id: "dest.md" if node_id == x else None,
    )
    assert all(op.node_id != x for op in outcome.ops)
    assert not any(op.kind == "deleted" for op in outcome.ops)


def test_compute_ops_hard_deletes_when_anchor_elsewhere_finds_nothing():
    """A fake ``anchor_elsewhere`` returning ``None`` still hard-deletes (E06 parity).

    When the scan proves the anchor exists NOWHERE else, this is the
    strongest available evidence of a genuine delete -- spec §6.2 E06
    requires the S0 hard-delete to still fire, unweakened.
    """
    x = "sy5phfxx"
    base = parse(_managed(f"Shared text {contract_anchor(x)}\n"))
    vault_text = _managed("")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({x: "S0"}),
        projection=ProjectionIndex(),
        current_path="source.md",
        anchor_elsewhere=lambda node_id: None,
    )
    assert [(op.kind, op.node_id) for op in outcome.ops] == [("deleted", x)]


def test_compute_ops_default_anchor_elsewhere_preserves_prior_behavior():
    """Omitting ``anchor_elsewhere`` (the default, ``None``) behaves exactly
    like before this fix -- a plain S0 delete with no cross-file evidence at
    all still hard-deletes (this is ``test_diff_blocks_detects_deleted_s0``'s
    own scenario, re-asserted here to pin the default explicitly)."""
    x = "2ha7cfbt"
    base = parse(_managed(f"Temporary note {contract_anchor(x)}\n"))
    vault_text = _managed("")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text="",
        vault_text=vault_text,
        maturity=_maturity_map({x: "S0"}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert [(op.kind, op.node_id) for op in outcome.ops] == [("deleted", x)]


# --- ProjectionIndex --------------------------------------------------------------


def test_projection_index_update_transfers_ownership():
    index = ProjectionIndex()
    index.update("a.md", {"x1", "x2"})
    assert index.owner("x1") == "a.md"
    assert index.owner("x2") == "a.md"

    # a.md drops x1, b.md claims it.
    index.update("a.md", {"x2"})
    assert index.owner("x1") is None
    index.update("b.md", {"x1"})
    assert index.owner("x1") == "b.md"
    assert index.owner("x2") == "a.md"


def test_apply_repairs_reinserts_lost_anchor_and_id_produces_no_op():
    x = "tpuqytxy"
    base_text = _managed(f"Keep this text {contract_anchor(x)}\n")
    base = parse(base_text)
    # Anchor stripped, text otherwise byte-identical -> certain repair.
    vault_text = _managed("Keep this text\n")
    vault = parse(vault_text)
    outcome = diff_blocks(
        base,
        vault,
        base_text=base_text,
        vault_text=vault_text,
        maturity=_maturity_map({x: "S0"}),
        projection=ProjectionIndex(),
        current_path="vault.md",
    )
    assert any(r.code == "E_LOST_ANCHOR" and r.id == x for r in outcome.lint.repairs)
    assert f"Keep this text {contract_anchor(x)}" in outcome.repaired_text
    # Repaired -> present in both B and V' with identical text -> no op.
    assert all(op.node_id != x for op in outcome.ops)


# =================================================================================
# Reconciler integration tests (real sqlite + real temp files)
# =================================================================================


def test_quiet_shortcut_makes_no_writes(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    text = _managed("")
    path = tmp_path / "note.md"
    path.write_text(text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), text)

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    mtime_before = path.stat().st_mtime_ns

    reconciler.on_change(str(path))

    assert path.read_text(encoding="utf-8") == text
    assert path.stat().st_mtime_ns == mtime_before
    assert conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 0


def test_hub_only_shortcut_writes_hub_projection_and_puts_base(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = "gicvtn5i"
    _seed_node(conn, x, "claim", "original text")

    base_text = render(parse(_managed(f"original text {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    # Hub-side edit only; the vault file is left untouched (V == B).
    store.commit_node(
        conn, x, new_body="hub-edited text", change_class="patch", facets_touched=[], author="human"
    )

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    final = path.read_text(encoding="utf-8")
    assert "hub-edited text" in final
    assert base_store.get(conn, root_id, str(path)) == final
    new_hash = object_hash(final.encode("utf-8"))
    assert origin.is_echo(str(path), new_hash)


def test_conflict_skips_apply_and_preserves_vault_version_in_cause_ref(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = "kseuqg5j"
    _seed_node(conn, x, "claim", "line one")

    base_text = render(parse(_managed(f"line one {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    base_store.put(conn, root_id, str(path), base_text)

    # Concurrent hub edit.
    store.commit_node(
        conn, x, new_body="hub changed", change_class="patch", facets_touched=[], author="human"
    )
    # Divergent vault edit.
    vault_text = _managed(f"vault changed {contract_anchor(x)}\n")
    path.write_text(vault_text, encoding="utf-8")

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    # Hub head is untouched by the conflicting op.
    assert store.get_node(conn, x).body == "hub changed\n"

    rows = conn.execute(
        "SELECT node_id, cause_kind, cause_ref FROM review_queue WHERE cause_kind='conflict'"
    ).fetchall()
    assert len(rows) == 1
    node_id, cause_kind, cause_ref = rows[0]
    assert node_id == x
    payload = json.loads(cause_ref)
    assert payload["vault_text"] == "vault changed"
    assert payload["base_text"] == "line one"

    # Hub wins the file this cycle.
    final = path.read_text(encoding="utf-8")
    assert "hub changed" in final
    assert "vault changed" not in final


def test_convergent_edit_is_a_no_op_not_a_conflict(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = "murwjnpd"
    _seed_node(conn, x, "claim", "line one")

    base_text = render(parse(_managed(f"line one {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    base_store.put(conn, root_id, str(path), base_text)

    store.commit_node(
        conn, x, new_body="agreed text", change_class="patch", facets_touched=[], author="human"
    )
    vault_text = _managed(f"agreed text {contract_anchor(x)}\n")
    path.write_text(vault_text, encoding="utf-8")

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    assert conn.execute("SELECT COUNT(*) FROM review_queue WHERE cause_kind='conflict'").fetchone()[
        0
    ] == 0
    assert store.get_node(conn, x).body == "agreed text\n"


def test_pause_makes_zero_writes(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x, y = "pakprpmm", "pit7kgjj"
    _seed_node(conn, x, "claim", "alpha")
    _seed_node(conn, y, "claim", "beta")

    base_text = render(
        parse(_managed(f"alpha {contract_anchor(x)}\nbeta {contract_anchor(y)}\n"))
    )
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    bad_id = "aaaaaaab"  # checksum-invalid -> E_ID_CHECKSUM, 1 of 2 base blocks -> 50% > 25%
    new_line = f"new line {contract_anchor(bad_id)}\n"
    vault_text = _managed(f"alpha {contract_anchor(x)}\nbeta {contract_anchor(y)}\n{new_line}")
    path.write_text(vault_text, encoding="utf-8")

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    # Zero writes: file untouched, base_store untouched.
    assert path.read_text(encoding="utf-8") == vault_text
    assert base_store.get(conn, root_id, str(path)) == base_text

    rows = conn.execute(
        "SELECT cause_ref FROM review_queue WHERE cause_kind='violation'"
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["pause"] is True


def test_tm_new_mint_rewrite_and_origin_recorded(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    path = tmp_path / "note.md"
    vault_text = _managed("Write the design doc ^tm-new\n")
    path.write_text(vault_text, encoding="utf-8")

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    final = path.read_text(encoding="utf-8")
    assert "^tm-new" not in final
    assert "Write the design doc" in final

    nodes = conn.execute("SELECT id, node_type FROM nodes").fetchall()
    assert len(nodes) == 1
    new_id, node_type = nodes[0]
    assert node_type == "claim"
    assert contract_anchor(new_id) in final

    # write_if_diff records the origin write for the ^tm-new rewrite.
    new_hash = object_hash(final.encode("utf-8"))
    assert origin.is_echo(str(path), new_hash)

    # base_store.put landed the same content.
    assert base_store.get(conn, root_id, str(path)) == final


def test_echo_recording_matches_write_if_diff_hash(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = "qd7rsed6"
    _seed_node(conn, x, "claim", "line one")
    base_text = render(parse(_managed(f"line one {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    store.commit_node(
        conn, x, new_body="line two", change_class="patch", facets_touched=[], author="human"
    )

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    final = path.read_text(encoding="utf-8")
    expected_hash = object_hash(final.encode("utf-8"))
    # Matching hash + path is consumed exactly once.
    assert origin.is_echo(str(path), expected_hash)
    assert not origin.is_echo(str(path), expected_hash)


def test_base_store_put_called_with_h2(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = "qkiaz37u"
    _seed_node(conn, x, "claim", "line one")
    base_text = render(parse(_managed(f"line one {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    vault_text = _managed(f"line two {contract_anchor(x)}\n")
    path.write_text(vault_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    final = path.read_text(encoding="utf-8")
    assert base_store.get(conn, root_id, str(path)) == final
    assert "line two" in final


def test_moved_op_is_a_hub_no_op_but_reflected_in_base(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    a, b = "r3wa6huv", "rwfxvl3q"
    _seed_node(conn, a, "task", "Task Alpha", task_state="open")
    _seed_node(conn, b, "task", "Task Beta", task_state="open")

    base_text = render(
        parse(
            _managed(
                f"- [ ] Task Alpha {contract_anchor(a)}\n- [ ] Task Beta {contract_anchor(b)}\n"
            )
        )
    )
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    vault_text = _managed(
        f"- [ ] Task Beta {contract_anchor(b)}\n- [ ] Task Alpha {contract_anchor(a)}\n"
    )
    path.write_text(vault_text, encoding="utf-8")

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    final = path.read_text(encoding="utf-8")
    assert final == vault_text
    # No kernel-level mutation happened for the moved nodes.
    assert store.get_node(conn, a).body == "Task Alpha\n"
    assert store.get_node(conn, b).body == "Task Beta\n"


def test_unregistered_path_is_ignored(tmp_path):
    conn = _conn()
    path = tmp_path / "note.md"
    path.write_text(_managed("hello\n"), encoding="utf-8")
    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    # No sync root registered at all -- must not raise.
    reconciler.on_change(str(path))
    assert conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 0


def test_conservative_root_routes_certain_repairs_to_review_instead_of_applying(tmp_path):
    conn = _conn()
    cloud_dir = tmp_path / "OneDrive" / "vault"
    cloud_dir.mkdir(parents=True)
    root_id = _register_root(conn, cloud_dir)
    x = "vqche7yn"
    _seed_node(conn, x, "claim", "Keep this text")
    # Four extra, untouched blocks so the single lost-anchor id stays under
    # the 25% pause threshold (1/5 == 20%) -- isolates conservative-profile
    # repair routing from the (separately tested) pause&diff guard.
    padding_ids = ["ys5ek7ih", "zzqowp2r", "23zl56h5", "24qmgnvr"]
    for i, pid in enumerate(padding_ids):
        _seed_node(conn, pid, "claim", f"padding {i}")
    padding_lines = "".join(
        f"padding {i} {contract_anchor(pid)}\n" for i, pid in enumerate(padding_ids)
    )

    base_text = render(
        parse(_managed(f"Keep this text {contract_anchor(x)}\n{padding_lines}"))
    )
    path = cloud_dir / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    # Strip the anchor but keep the text byte-identical -> a CERTAIN
    # E_LOST_ANCHOR repair under a normal root; under a conservative
    # (cloud-synced) root it must be routed to review instead, never
    # applied silently, and the id must never be misread as "deleted".
    vault_text = _managed(f"Keep this text\n{padding_lines}")
    path.write_text(vault_text, encoding="utf-8")

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    assert store.get_node(conn, x).status == "live"

    rows = conn.execute(
        "SELECT cause_ref FROM review_queue WHERE cause_kind='violation'"
    ).fetchall()
    assert any(json.loads(r[0]).get("code") == "E_LOST_ANCHOR" for r in rows)


# =================================================================================
# §7 metrics producers (task T9.2c): real on_change cycles feed
# metrics.record_sync_cycle_ms / metrics.record_auto_repair.
# =================================================================================


def test_on_change_records_sync_cycle_ms_for_quiet_cycle(tmp_path):
    """A quiet cycle (V == B == H) still counts as one attempted sync cycle."""
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    text = _managed("")
    path = tmp_path / "note.md"
    path.write_text(text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), text)

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    durations, auto_repairs = metrics._recorder.snapshot()
    assert len(durations) == 1
    assert durations[0] >= 0.0
    assert auto_repairs == {}


def test_on_change_records_auto_repair_for_silently_applied_certain_repair(tmp_path):
    """A non-conservative root's certain E_LOST_ANCHOR repair is silently applied
    (never routed to review) -- exactly one repair must be recorded for it.
    """
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = "vqwr7yne"
    _seed_node(conn, x, "claim", "Keep this text")
    # Four extra, untouched blocks so the single lost-anchor id stays under
    # the 25% pause threshold (1/5 == 20%) -- isolates the silently-applied
    # certain-repair case from the (separately tested) pause&diff guard.
    padding_ids = ["ys5ek7ih", "zzqowp2r", "23zl56h5", "24qmgnvr"]
    for i, pid in enumerate(padding_ids):
        _seed_node(conn, pid, "claim", f"padding {i}")
    padding_lines = "".join(
        f"padding {i} {contract_anchor(pid)}\n" for i, pid in enumerate(padding_ids)
    )

    base_text = render(
        parse(_managed(f"Keep this text {contract_anchor(x)}\n{padding_lines}"))
    )
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    # Strip the anchor but keep the text byte-identical -> a CERTAIN
    # E_LOST_ANCHOR repair (spec §4.7), applied silently under a normal
    # (non-conservative) root.
    vault_text = _managed(f"Keep this text\n{padding_lines}")
    path.write_text(vault_text, encoding="utf-8")

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    # The repair really was applied silently: the anchor is back, live, and
    # never went to review.
    assert store.get_node(conn, x).status == "live"
    assert conn.execute(
        "SELECT COUNT(*) FROM review_queue WHERE cause_kind='violation'"
    ).fetchone()[0] == 0

    durations, auto_repairs = metrics._recorder.snapshot()
    assert len(durations) == 1
    assert auto_repairs == {"E_LOST_ANCHOR": 1}
    assert metrics.compute_metrics(conn)["auto_repairs"] == {"E_LOST_ANCHOR": 1}


def test_conservative_root_routing_does_not_record_auto_repair(tmp_path):
    """The SAME certain-repair, under a conservative (cloud-synced) root, is
    routed to review instead of applied -- must NOT be recorded (no double-count
    against the silently-applied case above).
    """
    conn = _conn()
    cloud_dir = tmp_path / "OneDrive" / "vault"
    cloud_dir.mkdir(parents=True)
    root_id = _register_root(conn, cloud_dir)
    x = "vqche7yn"
    _seed_node(conn, x, "claim", "Keep this text")
    # Four extra, untouched blocks so the single lost-anchor id stays under
    # the 25% pause threshold (1/5 == 20%) -- isolates conservative-profile
    # repair routing from the (separately tested) pause&diff guard.
    padding_ids = ["ys5ek7ih", "zzqowp2r", "23zl56h5", "24qmgnvr"]
    for i, pid in enumerate(padding_ids):
        _seed_node(conn, pid, "claim", f"padding {i}")
    padding_lines = "".join(
        f"padding {i} {contract_anchor(pid)}\n" for i, pid in enumerate(padding_ids)
    )

    base_text = render(
        parse(_managed(f"Keep this text {contract_anchor(x)}\n{padding_lines}"))
    )
    path = cloud_dir / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    vault_text = _managed(f"Keep this text\n{padding_lines}")
    path.write_text(vault_text, encoding="utf-8")

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    # Confirm the repair really was routed to review, not applied.
    rows = conn.execute(
        "SELECT cause_ref FROM review_queue WHERE cause_kind='violation'"
    ).fetchall()
    assert any(json.loads(r[0]).get("code") == "E_LOST_ANCHOR" for r in rows)

    # The cycle itself still counts (timing), but no repair was applied.
    durations, auto_repairs = metrics._recorder.snapshot()
    assert len(durations) == 1
    assert auto_repairs == {}


# =================================================================================
# Lossless container (task T5.8-2, human-decided 2026-07-13, fable-designed)
# =================================================================================


def test_hub_state_for_keeps_missing_node_but_drops_tombstone():
    """Missing-node block survives with skeleton text; a tombstoned block is dropped."""
    conn = _conn()
    missing_id = ids.mint()  # never created -> store.NodeNotFoundError
    tomb_id = ids.mint()
    _seed_node(conn, tomb_id, "claim", "will be tombstoned")
    conn.execute("UPDATE nodes SET status='tombstone' WHERE id=?", (tomb_id,))

    structure = parse(
        _managed(
            f"Missing node claim {contract_anchor(missing_id)}\n"
            f"Tombstoned claim {contract_anchor(tomb_id)}\n"
        )
    )
    result = reconcile.hub_state_for(conn, structure)

    assert missing_id in result.blocks
    assert result.blocks[missing_id].text == "Missing node claim"
    assert tomb_id not in result.blocks
    # No spurious review for the missing-node case (it was already reviewed
    # as E_ID_CHECKSUM/E_UNKNOWN_ANCHOR when it first appeared vault-side).
    assert conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 0


def test_first_cycle_prose_and_anchors_is_byte_preserving_then_quiet(tmp_path):
    """First reconcile of a prose+anchors file preserves every byte; the 2nd is quiet."""
    conn = _conn()
    _register_root(conn, tmp_path)
    x = ids.mint()
    _seed_node(conn, x, "claim", "A real claim")

    text = _managed(
        "Some prose above the claim.\n"
        f"A real claim {contract_anchor(x)}\n"
        "\n"
        "More trailing prose.\n"
    )
    path = tmp_path / "note.md"
    path.write_text(text, encoding="utf-8")
    # First-ever cycle: nothing registered in base_store yet for this path.

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    first_cycle_text = path.read_text(encoding="utf-8")
    assert "Some prose above the claim." in first_cycle_text
    assert "More trailing prose." in first_cycle_text
    assert first_cycle_text == text
    assert conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 0

    mtime_after_first = path.stat().st_mtime_ns
    reconciler.on_change(str(path))
    assert path.stat().st_mtime_ns == mtime_after_first
    assert path.read_text(encoding="utf-8") == first_cycle_text
    assert conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 0


# =================================================================================
# Golden fixtures
# =================================================================================

GOLDEN_CASES = ["modified", "checkbox", "create-tm-new", "delete-s0", "move", "conflict"]


@pytest.mark.parametrize("case", GOLDEN_CASES)
def test_golden_reconcile_case(tmp_path, case):
    case_dir = GOLDEN_ROOT / case
    hub_specs = json.loads((case_dir / "hub.json").read_text(encoding="utf-8"))
    vault_text = (case_dir / "vault.md").read_text(encoding="utf-8")
    expected_text = (case_dir / "expected.md").read_text(encoding="utf-8")
    expected_ops = json.loads((case_dir / "expected_ops.json").read_text(encoding="utf-8"))
    base_path = case_dir / "base.md"

    conn = _conn()
    root_id = _register_root(conn, tmp_path)

    for spec in hub_specs:
        _seed_node(
            conn,
            spec["id"],
            spec["node_type"],
            spec["body"],
            task_state=spec.get("task_state"),
        )
        if "hub_edit_body" in spec:
            store.commit_node(
                conn,
                spec["id"],
                new_body=spec["hub_edit_body"],
                change_class="patch",
                facets_touched=[],
                author="human",
            )

    path = tmp_path / "note.md"
    if base_path.exists():
        base_text = base_path.read_text(encoding="utf-8")
        base_store.put(conn, root_id, str(path), base_text)

    path.write_text(vault_text, encoding="utf-8")

    ids_before = {r[0] for r in conn.execute("SELECT id FROM nodes").fetchall()}

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)

    # Capture the ops actually detected via a thin spy on diff_blocks so the
    # golden comparison reflects the pipeline's real detection, not a
    # reimplementation.
    captured: dict[str, list] = {}
    real_diff_blocks = reconcile.diff_blocks

    def _spy(*args, **kwargs):
        outcome = real_diff_blocks(*args, **kwargs)
        captured["ops"] = outcome.ops
        return outcome

    reconcile.diff_blocks = _spy
    try:
        reconciler.on_change(str(path))
    finally:
        reconcile.diff_blocks = real_diff_blocks

    final_text = path.read_text(encoding="utf-8")

    if "{NEW}" in expected_text:
        ids_after = {r[0] for r in conn.execute("SELECT id FROM nodes").fetchall()}
        new_ids = ids_after - ids_before
        assert len(new_ids) == 1
        expected_text = expected_text.format(NEW=next(iter(new_ids)))

    assert final_text == expected_text

    actual_ops = [{"kind": op.kind, "node_id": op.node_id} for op in captured.get("ops", [])]
    assert actual_ops == expected_ops


# =================================================================================
# project_node_change (task T13.2, spec §4.8's hub-only branch + §1's
# "hub is the writer of record, each file-backed spoke is a projection"; the
# narrowest reading is docs/spec-questions.md's T13.3 entry)
# =================================================================================


def test_project_node_change_reprojects_owning_file(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = ids.mint()
    _seed_node(conn, x, "claim", "original text")

    base_text = render(parse(_managed(f"original text {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    # Hub-side mutation (mirrors an API-driven PATCH /nodes/{id} commit) --
    # nothing has touched the vault file itself.
    store.commit_node(
        conn, x, new_body="hub-edited text", change_class="patch", facets_touched=[], author="human"
    )

    origin = OriginTracker()
    reconciled = reconcile.project_node_change(conn, [x], origin)

    assert reconciled == [str(path)]
    final = path.read_text(encoding="utf-8")
    assert "hub-edited text" in final
    assert base_store.get(conn, root_id, str(path)) == final


def test_project_node_change_reprojects_checkbox_toggle(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = ids.mint()
    _seed_node(conn, x, "task", "Buy milk", task_state="open")

    base_text = render(parse(_managed(f"- [ ] Buy milk {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    store.commit_node(
        conn, x, task_state="done", change_class="patch", facets_touched=[], author="human"
    )

    origin = OriginTracker()
    reconciled = reconcile.project_node_change(conn, [x], origin)

    assert reconciled == [str(path)]
    final = path.read_text(encoding="utf-8")
    assert "- [x] Buy milk" in final


def test_project_node_change_unfiled_node_is_noop(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    # A separate, unrelated node IS filed -- proves the empty result below
    # is because the orphan is genuinely unowned, not because nothing in
    # the DB is filed at all.
    owned = ids.mint()
    _seed_node(conn, owned, "claim", "filed text")
    base_text = render(parse(_managed(f"filed text {contract_anchor(owned)}\n")))
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    # A node that was never projected into any managed file has no owner
    # in the ProjectionIndex.
    orphan = store.create_node(conn, node_type="claim", body="orphan text", author="human")

    reconciled = reconcile.project_node_change(conn, [orphan.id], OriginTracker())

    assert reconciled == []
    # And nothing else got touched either -- the filed file is untouched.
    assert path.read_text(encoding="utf-8") == base_text


def test_project_node_change_dedupes_paths_for_multiple_nodes_in_same_file(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = ids.mint()
    y = ids.mint()
    _seed_node(conn, x, "claim", "first text")
    _seed_node(conn, y, "claim", "second text")

    base_text = render(
        parse(_managed(f"first text {contract_anchor(x)}\n\nsecond text {contract_anchor(y)}\n"))
    )
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    store.commit_node(
        conn, x, new_body="first edited", change_class="patch", facets_touched=[], author="human"
    )
    store.commit_node(
        conn, y, new_body="second edited", change_class="patch", facets_touched=[], author="human"
    )

    origin = OriginTracker()
    reconciled = reconcile.project_node_change(conn, [x, y], origin)

    # Both ids own the SAME path -- exactly one reconcile call, not two.
    assert reconciled == [str(path)]
    final = path.read_text(encoding="utf-8")
    assert "first edited" in final
    assert "second edited" in final


def test_project_node_change_second_call_is_quiet_noop(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = ids.mint()
    _seed_node(conn, x, "claim", "original text")

    base_text = render(parse(_managed(f"original text {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    path.write_text(base_text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), base_text)

    store.commit_node(
        conn, x, new_body="hub-edited text", change_class="patch", facets_touched=[], author="human"
    )

    origin = OriginTracker()
    reconciled = reconcile.project_node_change(conn, [x], origin)
    assert reconciled == [str(path)]
    final = path.read_text(encoding="utf-8")
    assert "hub-edited text" in final

    # The base snapshot was updated by the first call's base_store.put(H2),
    # so a second immediate call has V == B == H: on_change's own quiet
    # shortcut returns before ever reaching write_if_diff/kernel_apply.
    # Commit count is the discriminating check -- unlike re-reading the
    # same bytes back off disk, it can't pass by accident if the pipeline
    # regressed into re-committing identical content on the second call.
    commits_before = conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0]

    # A second immediate call is idempotent end to end: still resolves the
    # same owning path (the node is still filed), performs zero writes,
    # and changes nothing on disk.
    reconciled_again = reconcile.project_node_change(conn, [x], origin)
    assert reconciled_again == [str(path)]
    assert conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == commits_before
    assert path.read_text(encoding="utf-8") == final


def test_project_node_change_skips_path_missing_from_disk(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = ids.mint()
    _seed_node(conn, x, "claim", "original text")

    base_text = render(parse(_managed(f"original text {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    base_store.put(conn, root_id, str(path), base_text)
    # Deliberately never write `path` to disk (or it vanished since the
    # ProjectionIndex was built) -- mirrors reconcile_all's own
    # FileNotFoundError handling exactly.

    store.commit_node(
        conn, x, new_body="hub-edited text", change_class="patch", facets_touched=[], author="human"
    )

    reconciled = reconcile.project_node_change(conn, [x], OriginTracker())

    assert reconciled == []
