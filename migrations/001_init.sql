CREATE TABLE objects   (hash TEXT PRIMARY KEY, kind TEXT NOT NULL,
                        bytes BLOB NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE nodes     (id TEXT PRIMARY KEY, node_type TEXT NOT NULL,
                        head_hash TEXT NOT NULL REFERENCES objects(hash),
                        maturity TEXT NOT NULL DEFAULT 'S0',
                        status TEXT NOT NULL DEFAULT 'live',
                        vetted INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE commits   (hash TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id),
                        parents TEXT NOT NULL,            -- JSON array of commit hashes
                        object_hash TEXT NOT NULL REFERENCES objects(hash),
                        change_class TEXT NOT NULL,       -- patch|minor|major
                        facets_touched TEXT NOT NULL,     -- JSON array of facet_ids
                        author TEXT NOT NULL,             -- token id
                        message TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL);
CREATE TABLE edges     (id TEXT PRIMARY KEY, src TEXT NOT NULL, dst TEXT NOT NULL,
                        edge_type TEXT NOT NULL, facet_binding TEXT,
                        provenance TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'track',
                        pinned_commit TEXT, created_at TEXT NOT NULL, retracted_at TEXT);
CREATE INDEX ix_edges_dst ON edges(dst) WHERE retracted_at IS NULL;
CREATE INDEX ix_edges_src ON edges(src) WHERE retracted_at IS NULL;
CREATE TABLE redirects  (old_id TEXT PRIMARY KEY, successors TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE review_queue (id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
                        cause_kind TEXT NOT NULL,  -- facet_break|subtasks_closed|evidence_retracted|recheck|conflict|violation|proposal
                        cause_ref TEXT, facet TEXT, created_at TEXT NOT NULL,
                        resolved_at TEXT, resolution TEXT);  -- still_holds|revised|retracted|dismissed
CREATE TABLE triggers   (id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
                        condition TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
                        enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE sync_files (path TEXT PRIMARY KEY, vault TEXT NOT NULL,
                        base_hash TEXT REFERENCES objects(hash),
                        contract_version INTEGER NOT NULL, last_synced_at TEXT);
CREATE TABLE tokens     (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                        class TEXT NOT NULL,               -- human|agent
                        secret_hash TEXT NOT NULL, rate_per_min INTEGER,
                        created_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE audit_log  (ts TEXT NOT NULL, token_id TEXT, action TEXT NOT NULL, detail TEXT);
CREATE VIRTUAL TABLE nodes_fts USING fts5(id UNINDEXED, body);
