-- M20-C (2026-09-24): there is no per-file `tm: <version>` marker any more; the grammar version is
-- hub-owned (`akasha.contract.grammar.CONTRACT_VERSION`). The column recorded that marker.
ALTER TABLE sync_files DROP COLUMN contract_version;
