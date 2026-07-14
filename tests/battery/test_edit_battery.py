"""Scripted edit battery E01-E20 (task T5.8, spec §6.2, §4.8, §4.7).

The M5 milestone closer: drives the REAL ``sync.reconcile`` / ``kernel.store``
/ ``sync.watcher`` / ``sync.base_store`` APIs end-to-end (never reimplements
reconcile logic here) against every scenario in spec §6.2's scripted edit
battery, and asserts a **silent-guess count of zero** across the cases whose
required outcome is "review / pause / ignore, not apply".

E-number -> fixture/test mapping
--------------------------------
* E01 modified   -- golden fixture (REUSED T5.4): golden/reconcile/modified/
* E02 checkbox   -- golden fixture (REUSED T5.4): golden/reconcile/checkbox/
* E03 move       -- golden fixture (REUSED T5.4): golden/reconcile/move/
* E04 cross-move -- golden fixture (NEW, two-file): golden/reconcile/e04-cross-file-move/
* E04b s0-cross-move -- golden fixture (NEW, two-file, task T5.8-3): the S0
  companion to E04 -- golden/reconcile/e04b-s0-cross-file-move/. Formerly
  KNOWN GAP #3 below (now RESOLVED, see that note).
* E05 cross-dup  -- golden fixture (NEW, two-file): golden/reconcile/e05-cross-file-dup/
* E06 delete-s0  -- golden fixture (REUSED T5.4): golden/reconcile/delete-s0/
* E07 delete-s1  -- golden fixture (NEW): golden/reconcile/e07-delete-s1/
* E08 create-new -- golden fixture (REUSED T5.4): golden/reconcile/create-tm-new/
* E09 crlf       -- golden fixture (NEW): golden/reconcile/e09-crlf-arrives/
* E10 nfd        -- golden fixture (NEW): golden/reconcile/e10-nfd-stable/
* E11 startup    -- behavioral (``reconcile.reconcile_all``); full coverage in
  T5.6's own ``tests/integration/test_crash_recovery.py``.
* E12 conflict   -- golden fixture (REUSED T5.4): golden/reconcile/conflict/
* E13 pause      -- behavioral + fixture data: golden/reconcile/e13-pause-storm/
* E14 fenced     -- behavioral + fixture data: golden/reconcile/e14-fenced-anchor-ignored/
* E15 checksum   -- behavioral + fixture data (KNOWN GAP, see below)
* E16 embed      -- golden fixture (NEW, two-file): golden/reconcile/e16-embed-shows-head/
* E17 reparent   -- golden fixture (NEW): golden/reconcile/e17-reparented/
* E18 debounce   -- behavioral (``sync.watcher.Debouncer``)
* E19 cloud-path -- behavioral (``sync.watcher.Watcher.load_roots``)
* E20 perf       -- behavioral (real ``Reconciler.on_change`` timing +
  ``tracemalloc``) -- SEE BLOCKER below.

Silent-guess counter (the DoD crux)
------------------------------------
A "silent guess" = the pipeline mutated hub state (committed/created/deleted
a node) for an anchor whose spec-required outcome is a review item / pause /
ignore, rather than an apply. Per-case, this is checked via
``store.history(conn, node_id)`` (or, for pause, every affected node's body)
taken BEFORE and AFTER ``on_change``: identical history/body == no silent
mutation. This is the crux definition used by ``_case_e05`` / ``_case_e07`` /
``_case_e13`` / ``_case_e14`` / ``_case_e15`` below (the five cases spec
explicitly requires "review/pause/ignore, not apply" for); each returns a
``silently_mutated: bool`` used both by its own dedicated test AND by
``test_silent_guess_count_across_battery`` (below), which reruns all five and
asserts the sum of violations is exactly 0. Every case ALSO asserts its own
POSITIVE expectation (exact op kind absent, exact review ``cause_ref`` code
present, or the pause/ignore behavior itself).

KNOWN GAPS found while building this battery (real pipeline behavior, NOT
fixed here -- ``reconcile.py``/``render.py`` are outside this task's Files
list; rule 8):

1. **E20 perf blocker.** A 5,000-block file's ``on_change`` measured
   ~11.5 s wall time (cProfile: ~5.4s / 90% of ``_lcs_ids``'s O(n^2) DP table
   inside ``reconcile._compute_ops``'s "moved" detection -- 24.98M calls to
   ``max()`` for n=m=5000), far exceeding the spec's <2s bound. This is a
   genuine algorithmic-complexity bug in ``reconcile.py`` (not a fixture or
   threshold problem) -- per this task's explicit instruction, the <2s bound
   below is NOT weakened and ``reconcile.py`` is NOT edited; the measured
   numbers are asserted honestly and reported as a blocker.
2. **RESOLVED (task T5.8-2, human-decided 2026-07-13, fable-designed).**
   Previously: ``render()``/``hub_state_for()`` only ever reproduced
   recognized blocks/embeds/refs, so any vault line whose EOL anchor id did
   not resolve to a live node (an ``E_ID_CHECKSUM``-malformed id, or -- more
   generally -- ANY free prose line that is not a block/embed/ref at all,
   e.g. a fenced example or plain narrative text) was silently dropped from
   the file the next time a write-back was triggered. The product owner
   decided a managed (``tm: 1``) file is a LOSSLESS CONTAINER: such lines
   now survive write-back verbatim by position via
   ``contract.parser.BlockSet.raw_lines``/``.front_matter`` and
   ``contract.render.render()``'s interleaving of them back in (see
   ``docs/mvp-spec.md`` §4.7's added sentence and the T5.8-2
   ``docs/spec-questions.md`` entry's ``Resolution:``). E14/E15's
   assertions below now also cover byte-survival directly (previously
   deliberately scoped away from it, per this same note in its prior form).
3. **RESOLVED (task T5.8-3, human-decided 2026-07-13, fable-designed).**
   Previously: ``_compute_ops``'s cross-file "deleted" branch only withheld
   a deletion when the id was ALREADY owned by another (already-reconciled)
   path, so a plain cross-file cut-paste of an S0 (not S1+) node was lossy
   under the "natural" processing order (the file that LOSES the anchor
   reconciled before the file that GAINS it) -- the S0 node was hard-deleted
   before the destination file ever got a chance to adopt it, and the
   destination then reported ``E_UNKNOWN_ANCHOR`` for a genuinely-lost node.
   E04 uses an S1+ node specifically to sidestep this (the ``E_DELETED_S1``
   withholding already kept ITS node alive across the two-file race). The
   fix adds an optional ``anchor_elsewhere`` callable to
   ``_compute_ops``/``diff_blocks`` (``None`` by default, preserving every
   existing pure test/golden byte-for-byte): ``Reconciler`` supplies the
   real implementation, a same-sync-root, lazily-cached scan of every OTHER
   ``*.md`` file's LIVE on-disk bytes for a genuine (parse-confirmed) EOL
   anchor match. A base-only id about to be hard-deleted is now withheld
   whenever that scan proves the anchor is still alive elsewhere (concrete
   evidence of a move-in-flight, not a guess); otherwise the hard-delete
   proceeds unchanged (E06's delete-s0 golden -- a one-file sync root with
   no "elsewhere" to find -- still hard-deletes, byte-identical). See
   E04b below (the new S0 companion fixture to E04) for both reconcile
   orderings (source-first AND dest-first) now converging losslessly.

Fixture-helper convention
---------------------------
``tests/`` has no ``__init__.py`` anywhere (not a package), so per the
existing convention established by ``tests/integration/test_conflict.py``
and ``tests/integration/test_crash_recovery.py`` (both of which copy, rather
than import, ``tests/unit/sync/test_reconcile.py``'s fixture helpers), the
helpers below are a self-contained copy of that same pattern, extended with
an OPTIONAL ``vetted``/``edges`` capability the shared T5.4 loader does not
need.
"""

from __future__ import annotations

import json
import sqlite3
import time
import tracemalloc
from pathlib import Path

import pytest

from akasha.contract import linter
from akasha.contract.parser import parse
from akasha.contract.render import render
from akasha.kernel import ids, store
from akasha.kernel.canonical import canonicalize_text
from akasha.kernel.ids import contract_anchor
from akasha.sync import base_store
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import Reconciler, reconcile_all
from akasha.sync.watcher import Debouncer, Watcher, detect_cloud_path

GOLDEN_ROOT = Path(__file__).resolve().parents[1] / "golden" / "reconcile"


# --- shared fixture helpers (copied convention, see module docstring) ----------


def _conn() -> sqlite3.Connection:
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return conn


def _seed_node(
    conn: sqlite3.Connection,
    node_id: str,
    node_type: str,
    body: str,
    task_state: str | None = None,
    *,
    vetted: bool = False,
) -> None:
    """Test-only fixture seeding under a CHOSEN id (genesis C0; see T5.4's precedent)."""
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
    if vetted:
        store.vet_node(conn, node_id)


def _managed(body: str) -> str:
    return canonicalize_text(f"---\ntm: 1\n---\n{body}")


def _register_root(conn: sqlite3.Connection, root_path: Path) -> str:
    return store.register_sync_root(conn, "vault", str(root_path))["id"]


def _read(path: Path) -> str:
    """Read a fixture file's exact bytes (never universal-newline-translated).

    ``Path.read_text()`` defaults to universal-newline translation, which
    would silently rewrite a committed CRLF fixture (E09) back to LF before
    it ever reached the pipeline -- exactly the bug this battery must not
    reintroduce. Mirrors ``tests/golden/test_serialization.py``'s own
    ``read_bytes()`` convention for the same reason.
    """
    return path.read_bytes().decode("utf-8")


def _seed_hub_from_json(conn: sqlite3.Connection, hub_specs: list[dict]) -> None:
    for spec in hub_specs:
        _seed_node(
            conn,
            spec["id"],
            spec["node_type"],
            spec["body"],
            task_state=spec.get("task_state"),
            vetted=spec.get("vetted", False),
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


# =================================================================================
# E01/E02/E03/E06/E08/E12 -- reused T5.4 golden fixtures, consumed under their
# E-numbers (rule 0.3: these six fixture directories are never edited by this
# task; only referenced).
# =================================================================================

REUSED_GOLDEN_CASES: dict[str, str] = {
    "E01": "modified",
    "E02": "checkbox",
    "E03": "move",
    "E06": "delete-s0",
    "E08": "create-tm-new",
    "E12": "conflict",
}

# Standard single-file 5-fixture-file cases this task adds (byte-exact
# expected.md + expected_ops.json, same harness as the reused six).
NEW_STANDARD_GOLDEN_CASES: dict[str, str] = {
    "E07": "e07-delete-s1",
    "E09": "e09-crlf-arrives",
    "E10": "e10-nfd-stable",
    "E17": "e17-reparented",
}


def _run_standard_golden_case(
    conn: sqlite3.Connection, tmp_path: Path, case_dir: Path
) -> tuple[str, list[dict]]:
    """Drive one standard single-file fixture dir through the real Reconciler.

    Mirrors ``tests/unit/sync/test_reconcile.py::test_golden_reconcile_case``
    (T5.4's own loader) exactly, extended only with the ``vetted`` hub.json
    field (needed for E07's S1+ node) via ``_seed_hub_from_json`` above.
    Returns ``(final_text, actual_ops)``.
    """
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    vault_text = _read(case_dir / "vault.md")
    base_path = case_dir / "base.md"

    root_id = _register_root(conn, tmp_path)
    _seed_hub_from_json(conn, hub_specs)

    path = tmp_path / "note.md"
    if base_path.exists():
        base_text = _read(base_path)
        base_store.put(conn, root_id, str(path), base_text)

    path.write_bytes(vault_text.encode("utf-8"))

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)

    import akasha.sync.reconcile as reconcile_module

    captured: dict[str, list] = {}
    real_diff_blocks = reconcile_module.diff_blocks

    def _spy(*args, **kwargs):
        outcome = real_diff_blocks(*args, **kwargs)
        captured["ops"] = outcome.ops
        return outcome

    reconcile_module.diff_blocks = _spy
    try:
        reconciler.on_change(str(path))
    finally:
        reconcile_module.diff_blocks = real_diff_blocks

    final_text = path.read_bytes().decode("utf-8")
    actual_ops = [{"kind": op.kind, "node_id": op.node_id} for op in captured.get("ops", [])]
    return final_text, actual_ops


@pytest.mark.parametrize("e_number,case_name", sorted(REUSED_GOLDEN_CASES.items()))
def test_reused_golden_case_passes_under_its_e_number(tmp_path, e_number, case_name):
    conn = _conn()
    case_dir = GOLDEN_ROOT / case_name
    expected_text = _read(case_dir / "expected.md")
    expected_ops = json.loads(_read(case_dir / "expected_ops.json"))

    final_text, actual_ops = _run_standard_golden_case(conn, tmp_path, case_dir)

    if "{NEW}" in expected_text:
        # create-tm-new: the minted id is nondeterministic; substitute it.
        new_ids = {r[0] for r in conn.execute("SELECT id FROM nodes").fetchall()}
        assert len(new_ids) == 1
        expected_text = expected_text.format(NEW=next(iter(new_ids)))

    assert final_text == expected_text, f"{e_number} ({case_name}): file mismatch"
    assert actual_ops == expected_ops, f"{e_number} ({case_name}): ops mismatch"


@pytest.mark.parametrize("e_number,case_name", sorted(NEW_STANDARD_GOLDEN_CASES.items()))
def test_new_standard_golden_case(tmp_path, e_number, case_name):
    conn = _conn()
    case_dir = GOLDEN_ROOT / case_name
    expected_text = _read(case_dir / "expected.md")
    expected_ops = json.loads(_read(case_dir / "expected_ops.json"))

    final_text, actual_ops = _run_standard_golden_case(conn, tmp_path, case_dir)

    assert final_text == expected_text, f"{e_number} ({case_name}): file mismatch"
    assert actual_ops == expected_ops, f"{e_number} ({case_name}): ops mismatch"


def test_e07_node_survives_hub_side_with_open_review():
    """E07 positive expectation beyond byte-equality: node preserved + reviewed."""
    conn = _conn()
    with _tmp_dir() as tmp_path:
        final_text, actual_ops = _run_standard_golden_case(
            conn, tmp_path, GOLDEN_ROOT / "e07-delete-s1"
        )
    assert actual_ops == []
    node = store.get_node(conn, "5ec7y5bu")
    assert node.status == "live"
    assert node.body == "Important claim\n"
    rows = store.find_open_reviews(conn, cause_kind="violation")
    codes = [json.loads(r["cause_ref"])["code"] for r in rows]
    assert "E_DELETED_S1" in codes


def test_e09_crlf_produces_zero_writes_and_zero_reviews():
    conn = _conn()
    with _tmp_dir() as tmp_path:
        case_dir = GOLDEN_ROOT / "e09-crlf-arrives"
        final_text, actual_ops = _run_standard_golden_case(conn, tmp_path, case_dir)
    assert actual_ops == []
    assert store.find_open_reviews(conn) == []


def test_e10_nfd_is_stable_across_a_second_reconcile(tmp_path):
    """E10's DoD requires a re-run to be quiet too, not just the first pass."""
    conn = _conn()
    case_dir = GOLDEN_ROOT / "e10-nfd-stable"
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    root_id = _register_root(conn, tmp_path)
    _seed_hub_from_json(conn, hub_specs)
    path = tmp_path / "note.md"
    base_store.put(conn, root_id, str(path), _read(case_dir / "base.md"))
    path.write_bytes(_read(case_dir / "vault.md").encode("utf-8"))

    reconciler = Reconciler(conn, OriginTracker())
    reconciler.on_change(str(path))
    mtime_after_first = path.stat().st_mtime_ns
    reconciler.on_change(str(path))
    assert path.stat().st_mtime_ns == mtime_after_first  # second run: zero further writes
    assert store.find_open_reviews(conn) == []


# =================================================================================
# E04 -- cross-file move (S1+ node; see module docstring KNOWN GAP #3 for the
# S0 case, which this fixture deliberately does not use).
# =================================================================================


def test_e04_cross_file_move_no_data_loss_not_delete_and_create(tmp_path):
    conn = _conn()
    case_dir = GOLDEN_ROOT / "e04-cross-file-move"
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    _seed_hub_from_json(conn, hub_specs)
    x = "6mvyqsqb"
    # Bump x to S1+ via one real inbound "supports" edge (spec §4.6 S1 rule).
    store.create_edge(
        conn, src="2kw7sr3j", dst=x, edge_type="supports", facet_binding="*", provenance="human"
    )
    assert store.get_maturity(conn, x) in {"S1", "S2", "S3", "S4"}

    root_id = _register_root(conn, tmp_path)
    source_path = tmp_path / "source.md"
    dest_path = tmp_path / "dest.md"

    base_store.put(conn, root_id, str(source_path), _read(case_dir / "source_base.md"))
    source_path.write_bytes(_read(case_dir / "source_base.md").encode("utf-8"))
    base_store.put(conn, root_id, str(dest_path), _read(case_dir / "dest_base.md"))
    dest_path.write_bytes(_read(case_dir / "dest_base.md").encode("utf-8"))

    # The cut-paste: source loses the anchor, dest gains the same anchor.
    source_path.write_bytes(_read(case_dir / "source_vault.md").encode("utf-8"))
    dest_path.write_bytes(_read(case_dir / "dest_vault.md").encode("utf-8"))

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)

    import akasha.sync.reconcile as reconcile_module

    captured: dict[str, list] = {}
    real_diff_blocks = reconcile_module.diff_blocks

    def _spy(*args, **kwargs):
        outcome = real_diff_blocks(*args, **kwargs)
        captured.setdefault("calls", []).append(outcome.ops)
        return outcome

    reconcile_module.diff_blocks = _spy
    try:
        # Processing order matters (module docstring KNOWN GAP #3 / T5.4's
        # ProjectionIndex docstring: "the source file already vacated it in
        # its own prior cycle"). Source first is the order that is lossless
        # for an S1+ node.
        reconciler.on_change(str(source_path))
        reconciler.on_change(str(dest_path))
    finally:
        reconcile_module.diff_blocks = real_diff_blocks

    source_final = source_path.read_bytes().decode("utf-8")
    dest_final = dest_path.read_bytes().decode("utf-8")
    assert source_final == _read(case_dir / "expected_source.md")
    assert dest_final == _read(case_dir / "expected_dest.md")

    # Dest's ops (the second diff_blocks call) match the golden expectation:
    # a "created" op with node_id set (cross-file ADOPT, not a fresh mint --
    # see reconcile.Op's own docstring on this exact distinction).
    dest_ops = [{"kind": op.kind, "node_id": op.node_id} for op in captured["calls"][1]]
    assert dest_ops == json.loads(_read(case_dir / "expected_ops.json"))

    # NOT a delete+create: exactly one node total, still live, same body.
    assert store.get_node(conn, x).status == "live"
    assert store.get_node(conn, x).body == "Shared text\n"
    assert c_count(conn) == 6  # x + citing node + 4 padding, never a spurious extra


def c_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]


# =================================================================================
# E04b -- cross-file move of an S0 (not S1+) node (task T5.8-3, the S0
# companion to E04): a plain cut-paste must be lossless under BOTH reconcile
# orderings -- source-first (the "natural causality" order that used to be
# lossy, see module docstring KNOWN GAP #3 -- now RESOLVED) and dest-first.
# =================================================================================


@pytest.mark.parametrize("order", ["source_first", "dest_first"])
def test_e04b_s0_cross_file_move_no_data_loss(tmp_path, order):
    conn = _conn()
    case_dir = GOLDEN_ROOT / "e04b-s0-cross-file-move"
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    _seed_hub_from_json(conn, hub_specs)
    x = "sy5phfxx"
    assert store.get_maturity(conn, x) == "S0"  # no inbound edges -- the E04 gap this covers

    root_id = _register_root(conn, tmp_path)
    source_path = tmp_path / "source.md"
    dest_path = tmp_path / "dest.md"

    base_store.put(conn, root_id, str(source_path), _read(case_dir / "source_base.md"))
    source_path.write_bytes(_read(case_dir / "source_base.md").encode("utf-8"))
    base_store.put(conn, root_id, str(dest_path), _read(case_dir / "dest_base.md"))
    dest_path.write_bytes(_read(case_dir / "dest_base.md").encode("utf-8"))

    # The cut-paste: source loses the anchor, dest gains the same anchor --
    # both file mutations land on disk before either reconcile cycle runs
    # (the race the two orderings below simulate).
    source_path.write_bytes(_read(case_dir / "source_vault.md").encode("utf-8"))
    dest_path.write_bytes(_read(case_dir / "dest_vault.md").encode("utf-8"))

    reconciler = Reconciler(conn, OriginTracker())
    if order == "source_first":
        reconciler.on_change(str(source_path))
        reconciler.on_change(str(dest_path))
    else:
        reconciler.on_change(str(dest_path))
        reconciler.on_change(str(source_path))

    source_final = source_path.read_bytes().decode("utf-8")
    dest_final = dest_path.read_bytes().decode("utf-8")
    assert source_final == _read(case_dir / "expected_source.md"), order
    assert dest_final == _read(case_dir / "expected_dest.md"), order

    # The node stays LIVE -- not hard-deleted -- and exactly one node exists
    # for it (never a spurious duplicate mint either).
    node = store.get_node(conn, x)
    assert node.status == "live"
    assert node.body == "Shared text\n"
    assert c_count(conn) == 5  # x + 4 padding, never a spurious extra

    # Never a silent, unrecoverable loss: no E_UNKNOWN_ANCHOR review for x
    # (dest-first MAY surface a harmless, self-explanatory E_DUP_ID review --
    # a true cross-file duplicate reads that way until the source file's own
    # cycle runs -- but the node itself is never lost nor silently guessed).
    rows = store.find_open_reviews(conn)
    codes = [json.loads(r["cause_ref"]).get("code") for r in rows]
    assert "E_UNKNOWN_ANCHOR" not in codes, (order, codes)


# =================================================================================
# E05 -- cross-file duplicate (copy WITHOUT cut): review via ProjectionIndex,
# never a silent apply, no data loss, no new node.
# =================================================================================


def _case_e05() -> bool:
    """Returns ``silently_mutated`` (True == a violation of the "review, not
    apply" invariant). Also performs the case's own positive assertions.
    """
    conn = _conn()
    case_dir = GOLDEN_ROOT / "e05-cross-file-dup"
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    _seed_hub_from_json(conn, hub_specs)
    x = "3iwckm6b"
    history_before = store.history(conn, x)

    with _tmp_dir() as tmp_path:
        root_id = _register_root(conn, tmp_path)
        source_path = tmp_path / "source.md"
        dest_path = tmp_path / "dest.md"
        # Source already durably owns x (established via base_store, never
        # reconciled again -- the user never touched source.md).
        base_store.put(conn, root_id, str(source_path), _read(case_dir / "source_base.md"))
        source_path.write_bytes(_read(case_dir / "source_base.md").encode("utf-8"))

        base_store.put(conn, root_id, str(dest_path), _read(case_dir / "dest_base.md"))
        dest_path.write_bytes(_read(case_dir / "dest_base.md").encode("utf-8"))
        # Copy-paste (no cut): dest gains a SECOND live copy of the same anchor.
        dest_path.write_bytes(_read(case_dir / "dest_vault.md").encode("utf-8"))

        origin = OriginTracker()
        reconciler = Reconciler(conn, origin)

        import akasha.sync.reconcile as reconcile_module

        captured: dict[str, list] = {}
        real_diff_blocks = reconcile_module.diff_blocks

        def _spy(*args, **kwargs):
            outcome = real_diff_blocks(*args, **kwargs)
            captured["ops"] = outcome.ops
            return outcome

        reconcile_module.diff_blocks = _spy
        try:
            reconciler.on_change(str(dest_path))
        finally:
            reconcile_module.diff_blocks = real_diff_blocks

        dest_final = dest_path.read_bytes().decode("utf-8")
        assert dest_final == _read(case_dir / "expected_dest.md")
        actual_ops = [{"kind": op.kind, "node_id": op.node_id} for op in captured.get("ops", [])]
        assert actual_ops == json.loads(_read(case_dir / "expected_ops.json"))

        rows = store.find_open_reviews(conn, node_id=x, cause_kind="violation")
        codes = [json.loads(r["cause_ref"])["code"] for r in rows]
        assert "E_DUP_ID" in codes
        assert c_count(conn) == 1  # never a second, spurious node created

    history_after = store.history(conn, x)
    return history_after != history_before


def test_e05_cross_file_dup_is_review_only_no_silent_apply():
    assert _case_e05() is False


# =================================================================================
# E07 -- silent-guess check (delete S1: review, no node mutation).
# =================================================================================


def _case_e07() -> bool:
    """Returns ``silently_mutated`` (True == the node was touched by on_change,
    which would be a violation -- E07 requires review-only, no data loss).
    """
    conn = _conn()
    x = "5ec7y5bu"
    case_dir = GOLDEN_ROOT / "e07-delete-s1"
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    _seed_hub_from_json(conn, hub_specs)
    history_before = store.history(conn, x)

    with _tmp_dir() as tmp_path:
        root_id = _register_root(conn, tmp_path)
        path = tmp_path / "note.md"
        base_store.put(conn, root_id, str(path), _read(case_dir / "base.md"))
        path.write_bytes(_read(case_dir / "vault.md").encode("utf-8"))
        Reconciler(conn, OriginTracker()).on_change(str(path))

        node = store.get_node(conn, x)
        assert node.status == "live"
        rows = store.find_open_reviews(conn, node_id=x, cause_kind="violation")
        codes = [json.loads(r["cause_ref"])["code"] for r in rows]
        assert "E_DELETED_S1" in codes

    history_after = store.history(conn, x)
    return history_after != history_before


def test_e07_silent_guess_check_isolated():
    """Standalone version of the E07 silent-guess check (also reused by the
    aggregate ``test_silent_guess_count_across_battery`` via ``_case_e07``)."""
    assert _case_e07() is False


# =================================================================================
# E13 -- pause & diff (formatter storm): zero writes, zero node mutations.
# =================================================================================


def _case_e13() -> bool:
    conn = _conn()
    case_dir = GOLDEN_ROOT / "e13-pause-storm"
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    _seed_hub_from_json(conn, hub_specs)
    ids_ = [spec["id"] for spec in hub_specs]
    histories_before = {i: store.history(conn, i) for i in ids_}

    with _tmp_dir() as tmp_path:
        root_id = _register_root(conn, tmp_path)
        path = tmp_path / "note.md"
        base_text = _read(case_dir / "base.md")
        base_store.put(conn, root_id, str(path), base_text)
        vault_text = _read(case_dir / "vault.md")
        path.write_bytes(vault_text.encode("utf-8"))

        Reconciler(conn, OriginTracker()).on_change(str(path))

        final_text = path.read_bytes().decode("utf-8")
        assert final_text == vault_text, "pause must make ZERO writes"
        assert base_store.get(conn, root_id, str(path)) == base_text, "base_store untouched"

        rows = store.find_open_reviews(conn, cause_kind="violation")
        payloads = [json.loads(r["cause_ref"]) for r in rows]
        assert any(p.get("pause") is True for p in payloads)

    histories_after = {i: store.history(conn, i) for i in ids_}
    return histories_after != histories_before


def test_e13_pause_makes_zero_writes_and_zero_mutations():
    assert _case_e13() is False


# =================================================================================
# E14 -- fake anchor inside a fenced code block: ignored at parse/lint time,
# never a violation, never a node mutation. Since task T5.8-2 (lossless
# container), the fence's RAW BYTES (including the fake anchor) are also
# now asserted to survive write-back verbatim -- see module docstring note 2.
# =================================================================================


def _case_e14() -> bool:
    conn = _conn()
    case_dir = GOLDEN_ROOT / "e14-fenced-anchor-ignored"
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    _seed_hub_from_json(conn, hub_specs)
    x = "2vza32ca"
    history_before = store.history(conn, x)

    # Pure parse/lint layer: the fenced fake anchor never becomes a block and
    # never raises a violation (spec §4.7: "Anything inside fenced code
    # blocks is ignored entirely").
    base_text = _read(case_dir / "base.md")
    block_set = parse(base_text)
    assert list(block_set.blocks.keys()) == [x]
    lint_result = linter.lint(block_set, block_set, base_text, {x: "S0"})
    assert lint_result.violations == []
    assert lint_result.review_items == []

    with _tmp_dir() as tmp_path:
        root_id = _register_root(conn, tmp_path)
        path = tmp_path / "note.md"
        base_store.put(conn, root_id, str(path), base_text)
        vault_text = _read(case_dir / "vault.md")
        path.write_bytes(vault_text.encode("utf-8"))
        Reconciler(conn, OriginTracker()).on_change(str(path))
        # Never any review/violation raised for the fenced fake anchor.
        assert store.find_open_reviews(conn) == []
        final_text = path.read_bytes().decode("utf-8")
        # The REAL claim's own content is preserved verbatim.
        assert "Real claim" in final_text
        # Lossless-container byte-survival (task T5.8-2): the fence
        # delimiters and the fake in-fence anchor line survive write-back
        # verbatim, not just the real claim -- the file is unchanged.
        assert "```" in final_text
        assert "Fake block ^tm-badbadb1" in final_text
        assert final_text == vault_text

    history_after = store.history(conn, x)
    return history_after != history_before


def test_e14_fenced_anchor_is_ignored_not_flagged():
    assert _case_e14() is False


# =================================================================================
# E15 -- malformed checksum: E_ID_CHECKSUM review, never a node mutation/guess.
# Since task T5.8-2 (lossless container), the malformed anchor's own line is
# also asserted to survive write-back verbatim -- see module docstring note 2.
# =================================================================================


def _case_e15() -> bool:
    conn = _conn()
    case_dir = GOLDEN_ROOT / "e15-malformed-checksum"
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    _seed_hub_from_json(conn, hub_specs)
    ids_before = {r[0] for r in conn.execute("SELECT id FROM nodes").fetchall()}

    with _tmp_dir() as tmp_path:
        root_id = _register_root(conn, tmp_path)
        path = tmp_path / "note.md"
        base_text = _read(case_dir / "base.md")
        base_store.put(conn, root_id, str(path), base_text)
        vault_text = _read(case_dir / "vault.md")
        path.write_bytes(vault_text.encode("utf-8"))

        Reconciler(conn, OriginTracker()).on_change(str(path))

        rows = store.find_open_reviews(conn, cause_kind="violation")
        codes = [json.loads(r["cause_ref"])["code"] for r in rows]
        assert "E_ID_CHECKSUM" in codes
        # Padding content (the legitimate blocks) survives untouched.
        final_text = path.read_bytes().decode("utf-8")
        for i in range(6):
            assert f"padding {i}" in final_text
        # Lossless-container byte-survival (task T5.8-2): the malformed
        # anchor's own line survives verbatim too -- the file is unchanged.
        assert "bad line ^tm-aaaaaaab" in final_text
        assert final_text == vault_text

    ids_after = {r[0] for r in conn.execute("SELECT id FROM nodes").fetchall()}
    # No silent guess: the malformed id NEVER becomes a real node (no mint,
    # no create, no delete of anything).
    return ids_after != ids_before


def test_e15_malformed_checksum_is_review_only_never_a_node():
    assert _case_e15() is False


# =================================================================================
# E16 -- embed added and target edited in hub: embed shows head.
# =================================================================================


def test_e16_embed_resolves_to_current_hub_head(tmp_path):
    conn = _conn()
    case_dir = GOLDEN_ROOT / "e16-embed-shows-head"
    hub_specs = json.loads(_read(case_dir / "hub.json"))
    _seed_hub_from_json(conn, hub_specs)
    x = "6e3nnequ"

    root_id = _register_root(conn, tmp_path)
    target_path = tmp_path / "target.md"
    note_path = tmp_path / "note.md"

    base_store.put(conn, root_id, str(target_path), _read(case_dir / "target_base.md"))
    target_path.write_bytes(_read(case_dir / "target_base.md").encode("utf-8"))
    base_store.put(conn, root_id, str(note_path), _read(case_dir / "note_base.md"))
    note_path.write_bytes(_read(case_dir / "note_base.md").encode("utf-8"))

    reconciler = Reconciler(conn, OriginTracker())
    # Target's own file reflects the hub edit (hub-only shortcut).
    reconciler.on_change(str(target_path))
    # The embedding file has nothing of its OWN that changed -- quiet.
    reconciler.on_change(str(note_path))

    target_final = target_path.read_bytes().decode("utf-8")
    note_final = note_path.read_bytes().decode("utf-8")
    assert target_final == _read(case_dir / "expected_target.md")
    assert note_final == _read(case_dir / "expected_note.md")

    # "Embed shows head": resolving the embed (as an Obsidian client would,
    # or render()'s own resolve_body plumbing) returns the CURRENT hub head,
    # not a stale snapshot -- proven via a direct render() call.
    resolved: list[str] = []

    def resolve_body(node_id: str) -> str:
        resolved.append(node_id)
        return store.get_node(conn, node_id).body

    render(parse(note_final), resolve_body=resolve_body)
    assert resolved == [x]
    assert store.get_node(conn, x).body == "updated body\n"


# =================================================================================
# E11 -- edits while daemon down (startup reconcile). Full coverage lives in
# T5.6's tests/integration/test_crash_recovery.py; this is a thin battery-level
# smoke test driving reconcile_all directly, per this task's own guidance.
# =================================================================================


def test_e11_startup_reconcile_applies_edits_made_while_down(tmp_path):
    conn = _conn()
    x = ids.mint()
    _seed_node(conn, x, "claim", "original")
    root_id = _register_root(conn, tmp_path)
    path = tmp_path / "note.md"
    base_text = render(parse(_managed(f"original {contract_anchor(x)}\n")))
    base_store.put(conn, root_id, str(path), base_text)
    path.write_text(base_text, encoding="utf-8")

    # Edited while no daemon/watcher was running at all.
    path.write_text(_managed(f"edited while down {contract_anchor(x)}\n"), encoding="utf-8")

    summary = reconcile_all(conn, OriginTracker())
    assert summary["files_missing"] == 0
    assert store.get_node(conn, x).body == "edited while down\n"

    # Idempotent second startup pass: zero further writes.
    mtime = path.stat().st_mtime_ns
    reconcile_all(conn, OriginTracker())
    assert path.stat().st_mtime_ns == mtime


# =================================================================================
# E18 -- rapid modify bursts: debounce, single cycle.
# =================================================================================


def test_e18_rapid_burst_yields_exactly_one_cycle():
    fired: list[str] = []
    debouncer = Debouncer(fired.append, debounce_seconds=0.5)
    for i in range(20):
        debouncer.notify("/vault/note.md", at=100.0 + i * 0.01)  # 100.00 .. 100.19
    assert debouncer.poll(at=100.5) == []  # still inside the window
    assert debouncer.poll(at=100.70) == ["/vault/note.md"]
    assert fired == ["/vault/note.md"]  # exactly one cycle for 20 raw events


# =================================================================================
# E19 -- vault under a simulated OneDrive path: warning + conservative profile.
# =================================================================================


def test_e19_cloud_sync_path_sets_conservative_profile(tmp_path, caplog):
    # NOTE: this test's own NAME must never contain "onedrive"/"dropbox" --
    # pytest's ``tmp_path`` fixture embeds the test node id as a path segment,
    # and ``detect_cloud_path`` matches ANY segment substring case-
    # insensitively, so a test named e.g. "..._onedrive_..." would make its
    # OWN ordinary (non-cloud) sibling directory a false-positive cloud match.
    import logging

    conn = _conn()
    cloud_dir = tmp_path / "OneDrive" / "vault"
    cloud_dir.mkdir(parents=True)
    store.register_sync_root(conn, "vault", str(cloud_dir))
    ordinary_dir = tmp_path / "plain-vault"
    ordinary_dir.mkdir()
    store.register_sync_root(conn, "vault2", str(ordinary_dir))

    watcher = Watcher(conn, lambda path: None)
    with caplog.at_level(logging.WARNING, logger="akasha"):
        roots = watcher.load_roots()

    by_path = {r.root_path: r for r in roots}
    assert by_path[str(cloud_dir)].conservative is True
    assert by_path[str(cloud_dir)].cloud_provider == "OneDrive"
    assert by_path[str(ordinary_dir)].conservative is False
    assert any("OneDrive" in rec.message for rec in caplog.records)
    assert detect_cloud_path(str(cloud_dir)) == "OneDrive"


# =================================================================================
# E20 -- 5,000-block file: cycle < 2s, memory bounded.
#
# BLOCKER (see module docstring KNOWN GAP #1): measured wall time is ~11.5s,
# not <2s. Root cause confirmed via cProfile: reconcile._compute_ops's "moved"
# detection calls _lcs_ids, an O(n^2) DP table, over the ~5000 stable
# (unmoved) ids every cycle -- 24.98M calls to builtins.max() dominate the
# profile (~90% of wall time) even though NOTHING moved in this scenario.
# This is a real algorithmic-complexity bug in reconcile.py, which is NOT in
# this task's Files list (rule 8) and is NOT edited here. The <2s bound below
# is asserted UNWEAKENED, per this task's explicit instruction to report
# rather than relax the threshold.
# =================================================================================


def test_e20_5000_block_cycle_perf_and_memory(tmp_path):
    conn = _conn()
    n = 5000
    node_ids = [ids.mint() for _ in range(n)]
    for i, node_id in enumerate(node_ids):
        _seed_node(conn, node_id, "claim", f"block number {i}")

    lines = "".join(f"block number {i} {contract_anchor(nid)}\n" for i, nid in enumerate(node_ids))
    root_id = _register_root(conn, tmp_path)
    path = tmp_path / "note.md"
    base_text = render(parse(_managed(lines)))
    base_store.put(conn, root_id, str(path), base_text)
    path.write_text(base_text, encoding="utf-8")

    # One real edit (the last block) forces a genuine diff cycle -- not the
    # quiet shortcut.
    modified_lines = "".join(
        (
            f"block number {i} {contract_anchor(nid)}\n"
            if i != n - 1
            else f"MODIFIED block {contract_anchor(nid)}\n"
        )
        for i, nid in enumerate(node_ids)
    )
    path.write_text(_managed(modified_lines), encoding="utf-8")

    reconciler = Reconciler(conn, OriginTracker())

    tracemalloc.start()
    t0 = time.perf_counter()
    reconciler.on_change(str(path))
    elapsed = time.perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak / 1e6
    print(f"\nE20 measured: elapsed={elapsed:.3f}s peak_traced_memory={peak_mb:.2f}MB")

    assert "MODIFIED block" in path.read_text(encoding="utf-8")

    # "memory bounded": a sane upper cap (narrowest reading -- spec gives no
    # exact number; 400MB gives generous headroom above the ~220MB observed
    # while still catching genuine unbounded growth).
    assert peak_mb < 400, f"peak traced memory {peak_mb:.2f}MB exceeds the 400MB sane cap"

    # Spec §6.2 E20: cycle < 2s. NOT weakened -- see module docstring KNOWN
    # GAP #1; this assertion is expected to fail against the current
    # reconcile.py implementation.
    assert elapsed < 2.0, (
        f"E20 perf gate: measured {elapsed:.3f}s, spec requires <2s. "
        "Root cause: reconcile._compute_ops's O(n^2) _lcs_ids over the "
        "unmoved-id set (see module docstring KNOWN GAP #1). Out of this "
        "task's Files list to fix (reconcile.py); reported as a blocker, "
        "not weakened."
    )


# =================================================================================
# Silent-guess counter: the DoD crux. Reruns every "review/pause/ignore, not
# apply" case's own check function and asserts the total violation count is 0.
# =================================================================================


def test_silent_guess_count_across_battery():
    violations = {
        "E05": _case_e05(),
        "E07": _case_e07(),
        "E13": _case_e13(),
        "E14": _case_e14(),
        "E15": _case_e15(),
    }
    total = sum(1 for v in violations.values() if v)
    assert total == 0, f"silent-guess violations detected: {violations}"


# --- tiny local contextmanager (tests/ has no shared conftest.py fixture for this) --


class _tmp_dir:
    """Minimal ``tempfile.TemporaryDirectory`` wrapper returning a ``Path``.

    Used by the standalone ``_case_e0N()`` helper functions above, which are
    plain functions (not pytest test functions receiving the ``tmp_path``
    fixture) so they can be reused identically by both their own dedicated
    test AND the aggregate ``test_silent_guess_count_across_battery``.
    """

    def __enter__(self) -> Path:
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        return Path(self._tmpdir.name)

    def __exit__(self, *exc_info: object) -> None:
        self._tmpdir.cleanup()
