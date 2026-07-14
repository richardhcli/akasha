"""Per-file base store: the "B" input of the §4.8 three-way reconcile.

Stores/retrieves the last-agreed *canonical* bytes for one managed file,
scoped to its durable sync root (spec §4.8: ``B = base_store.get(path)``;
§4.4: ``objects`` blob table, ``sync_files.base_hash``).

This module is deliberately thin (build-plan rule 0.4 — the only module
that writes SQLite is ``kernel/store.py``): it canonicalizes input text
(``kernel.canonical.canonicalize_text``, spec §4.3) and delegates every
persistent read/write to ``kernel/store.py`` helpers
(``write_base_snapshot``/``read_base_snapshot``/``sync_root_exists``), which
were added there per the same rule-0.4 precedent as T4.2's
``append_audit``, T4.4's ``vet_node``, T4.5's token helpers, and T4.6's
``enqueue_review`` (see the SPEC-QUESTION logged in
``docs/spec-questions.md`` for T5.1, since ``kernel/store.py`` is not in
this task's ``Files`` list).

Bytes-vs-str decision: this module's public API is ``str`` in, ``str`` out
(never raw ``bytes``). Spec §4.8's pseudocode treats ``V``/``B``/``H`` as
canonical text produced by ``canonicalize()``/``render()``, both of which
operate on ``str`` elsewhere in this codebase
(``kernel.canonical.canonicalize_text: str -> str``,
``contract.render.render: ... -> str``); UTF-8 bytes only exist at the
SQLite ``objects.bytes`` BLOB boundary, which is entirely encapsulated
inside ``kernel/store.py``. Callers of this module (T5.4's reconcile
pipeline) never see raw bytes.

Never store non-canonical input as-is: ``put`` always canonicalizes before
persisting, so ``get`` only ever returns canonical text (or ``None`` for an
unset/fresh path).
"""

from __future__ import annotations

import sqlite3

from akasha.kernel import store
from akasha.kernel.canonical import canonicalize_text


def put(conn: sqlite3.Connection, sync_root_id: str, path: str, data: str) -> None:
    """Record ``data``'s canonical form as ``path``'s new last-agreed base snapshot.

    ``data`` is canonicalized (spec §4.3) before being persisted — the
    caller may pass raw (non-canonical) file contents; only the canonical
    form is ever stored. Raises ``kernel.store.SyncRootNotFoundError`` if
    ``sync_root_id`` is not a durably registered sync root (T4.10). Content
    is stored content-addressed (``kernel/store.py::write_base_snapshot``),
    so re-``put``ting identical canonical bytes reuses the existing
    ``objects`` row rather than duplicating it.
    """
    canonical_text = canonicalize_text(data)
    store.write_base_snapshot(conn, sync_root_id, path, canonical_text)


def get(conn: sqlite3.Connection, sync_root_id: str, path: str) -> str | None:
    """Return ``path``'s last-agreed canonical base text, or ``None`` if unset.

    Scoped to ``sync_root_id``: a base snapshot recorded under a different
    sync root (or a path that was never ``put``) reads as ``None``, same as
    a wholly fresh path.
    """
    return store.read_base_snapshot(conn, sync_root_id, path)
