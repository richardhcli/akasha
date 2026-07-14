-- M4 contract refinement: durable sync roots and subjectless create proposals.
--
-- M5 has not shipped, so sync_files is normally empty. The copy path still
-- preserves any development rows by treating the former vault label as both
-- the legacy root id/name/path; the registration can then be updated through
-- POST /v1/sync/roots before watching starts.

CREATE TABLE sync_roots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    root_path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

INSERT INTO sync_roots (id, name, root_path, created_at)
SELECT vault, vault, vault, datetime('now')
FROM sync_files
GROUP BY vault;

CREATE TABLE sync_files_new (
    path TEXT PRIMARY KEY,
    sync_root_id TEXT NOT NULL REFERENCES sync_roots(id),
    base_hash TEXT REFERENCES objects(hash),
    contract_version INTEGER NOT NULL,
    last_synced_at TEXT
);

INSERT INTO sync_files_new (path, sync_root_id, base_hash, contract_version, last_synced_at)
SELECT path, vault, base_hash, contract_version, last_synced_at
FROM sync_files;

DROP TABLE sync_files;
ALTER TABLE sync_files_new RENAME TO sync_files;

CREATE TABLE review_queue_new (
    id TEXT PRIMARY KEY,
    node_id TEXT,
    cause_kind TEXT NOT NULL,
    cause_ref TEXT,
    facet TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT
);

INSERT INTO review_queue_new (
    id, node_id, cause_kind, cause_ref, facet, created_at, resolved_at, resolution
)
SELECT id, node_id, cause_kind, cause_ref, facet, created_at, resolved_at, resolution
FROM review_queue;

DROP TABLE review_queue;
ALTER TABLE review_queue_new RENAME TO review_queue;
