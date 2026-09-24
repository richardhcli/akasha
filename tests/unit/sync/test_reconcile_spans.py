"""Spans through the reconcile pipeline (M20-A/B/E): mint, mirror, multi-line, hub-side edit.

Real store, real files, real ``Reconciler``; the parser/render/linter details live in
``tests/unit/contract/test_spans.py``.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from akasha.kernel import store
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import Reconciler, project_node_change

_ID = re.compile(r"\{tm-([0-9a-z]{8})\}")


def _setup(tmp_path: Path) -> tuple[sqlite3.Connection, Reconciler]:
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    store.register_sync_root(conn, "vault", str(tmp_path))
    return conn, Reconciler(conn, OriginTracker())


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _mint(tmp_path: Path, reconciler: Reconciler, name: str, text: str) -> tuple[Path, str]:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    reconciler.on_change(str(path))
    found = _ID.search(_read(path))
    assert found, _read(path)
    return path, found.group(1)


def _reviews(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0])


def test_a_span_new_marker_is_minted_in_place_and_nothing_else_changes(tmp_path):
    conn, r = _setup(tmp_path)
    path, node = _mint(tmp_path, r, "a.md", "keep {a fresh idea}{tm-new} and this\n")
    assert _read(path) == f"keep {{a fresh idea}}{{tm-{node}}} and this\n"
    assert store.get_node(conn, node).body == "a fresh idea\n"
    assert _reviews(conn) == 0


def test_two_span_requests_on_one_line_are_both_minted(tmp_path):
    conn, r = _setup(tmp_path)
    path = tmp_path / "a.md"
    path.write_text("{one}{tm-new} then {two}{tm-new} end\n", encoding="utf-8")
    r.on_change(str(path))
    ids = _ID.findall(_read(path))
    assert len(ids) == 2 and len(set(ids)) == 2
    assert [store.get_node(conn, i).body for i in ids] == ["one\n", "two\n"]
    assert _read(path).endswith(" end\n") and "{tm-new}" not in _read(path)


def test_editing_a_span_in_one_file_changes_the_other_keeping_its_own_context(tmp_path):
    conn, r = _setup(tmp_path)
    a, node = _mint(tmp_path, r, "a.md", "A says {shared idea}{tm-new} in a sentence.\n")
    b = tmp_path / "b.md"
    b.write_text(f"B, padded: {{ shared idea }}{{tm-{node}}}; B's tail\n", encoding="utf-8")
    r.on_change(str(b))
    assert _reviews(conn) == 0

    a.write_text(_read(a).replace("shared idea", "better idea"), encoding="utf-8")
    r.on_change(str(a))

    assert store.get_node(conn, node).body == "better idea\n"
    assert _read(b) == f"B, padded: {{ better idea }}{{tm-{node}}}; B's tail\n"  # padding kept


def test_editing_the_padded_copy_propagates_without_padding(tmp_path):
    conn, r = _setup(tmp_path)
    a, node = _mint(tmp_path, r, "a.md", "{shared idea}{tm-new}\n")
    b = tmp_path / "b.md"
    b.write_text(f"[ {{  shared idea  }}{{tm-{node}}} ]\n", encoding="utf-8")
    r.on_change(str(b))
    before = _read(b)
    r.on_change(str(b))  # a quiet cycle: padding differences alone are never an edit
    assert _read(b) == before and store.get_node(conn, node).body == "shared idea\n"

    b.write_text(f"[ {{  edited  }}{{tm-{node}}} ]\n", encoding="utf-8")
    r.on_change(str(b))
    assert store.get_node(conn, node).body == "edited\n"
    assert _read(a) == f"{{edited}}{{tm-{node}}}\n"


def test_a_multi_line_span_is_mirrored_in_both_directions(tmp_path):
    conn, r = _setup(tmp_path)
    a, node = _mint(
        tmp_path, r, "a.md", "## Packing\n{ Bring the bag\n\nand the charger }{tm-new}\n"
    )
    b = tmp_path / "b.md"
    b.write_text(
        f"Trip notes\n{{Bring the bag\n\nand the charger}}{{tm-{node}}}\nmore\n", encoding="utf-8"
    )
    r.on_change(str(b))
    assert _reviews(conn) == 0

    a.write_text(_read(a).replace("the bag", "the RED bag"), encoding="utf-8")  # a NON-last line
    r.on_change(str(a))
    assert (
        "RED bag" in _read(b)
        and _read(b).startswith("Trip notes\n")
        and _read(b).endswith("\nmore\n")
    )

    b.write_text(_read(b).replace("charger", "CHARGER\n\nand a map"), encoding="utf-8")
    r.on_change(str(b))
    assert store.get_node(conn, node).body == "Bring the RED bag\n\nand the CHARGER\n\nand a map\n"
    assert (
        _read(a)
        == "## Packing\n{ Bring the RED bag\n\nand the CHARGER\n\nand a map }" + f"{{tm-{node}}}\n"
    )


def test_a_span_and_a_whole_line_block_can_mirror_each_other(tmp_path):
    conn, r = _setup(tmp_path)
    a = tmp_path / "a.md"
    a.write_text("the shared sentence ^tm-new\n", encoding="utf-8")
    r.on_change(str(a))
    node = re.search(r"\^tm-([0-9a-z]{8})", _read(a)).group(1)  # type: ignore[union-attr]
    b = tmp_path / "b.md"
    b.write_text(f"see {{the shared sentence}}{{tm-{node}}} here\n", encoding="utf-8")
    r.on_change(str(b))
    assert _reviews(conn) == 0

    b.write_text(f"see {{the shared sentence, edited}}{{tm-{node}}} here\n", encoding="utf-8")
    r.on_change(str(b))
    assert _read(a) == f"the shared sentence, edited ^tm-{node}\n"  # each keeps its own syntax


def test_a_hub_side_edit_rewrites_the_span_in_every_file(tmp_path):
    conn, r = _setup(tmp_path)
    a, node = _mint(tmp_path, r, "a.md", "x {old}{tm-new} y\n")
    b = tmp_path / "b.md"
    b.write_text(f"{{ old }}{{tm-{node}}}\n", encoding="utf-8")
    r.on_change(str(b))

    store.commit_node(
        conn, node, new_body="new\nsecond", change_class="patch", facets_touched=[], author="human"
    )
    project_node_change(conn, [node], OriginTracker())

    assert _read(a) == f"x {{new\nsecond}}{{tm-{node}}} y\n"
    assert _read(b) == f"{{ new\nsecond }}{{tm-{node}}}\n"


def test_damaged_span_ids_are_repaired_in_place_never_paused(tmp_path):
    conn, r = _setup(tmp_path)
    a, node = _mint(tmp_path, r, "a.md", "keep {shared text}{tm-new} ok\n")
    a.write_text("keep {shared text} ok\n", encoding="utf-8")  # the {tm-id} was deleted by accident
    r.on_change(str(a))
    assert _read(a) == f"keep {{shared text}}{{tm-{node}}} ok\n"  # restored, node untouched
    assert len(store.history(conn, node)) == 1 and _reviews(conn) == 0

    a.write_text(
        f"{{one}}{{tm-{node}}} and {{two}}{{tm-{node}}}\n", encoding="utf-8"
    )  # a duplicate
    r.on_change(str(a))
    ids = _ID.findall(_read(a))
    assert ids[0] == node and ids[1] != node
    assert [store.get_node(conn, i).body for i in ids] == ["one\n", "two\n"] or "shared" in _read(a)
    assert _reviews(conn) == 0
