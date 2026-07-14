# Spec questions

Log of ambiguities hit while implementing `docs/build-plan.md`. Per build-plan
rule 0.2 / rule 2: never invent schema, endpoints, ID formats, or grammar
beyond `docs/mvp-spec.md`. When something is ambiguous, implement the
narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and log an
entry here so a human can resolve it.

Entry format:

```
## <task ID> — <one-line question>
- **Where:** <file:line>
- **Narrowest reading taken:** <what was implemented in the meantime>
- **Resolution:** <filled in once a human answers; leave "open" until then>
```

This file holds **open** questions only. Resolved entries are moved to
`docs/archived-questions.md` in a batch when the milestone that raised them
closes (context-size optimization — an agent scanning for outstanding
ambiguities shouldn't have to read past closed ones). See that file for the
full resolved history: M1 (T1.3/T1.5/T1.6/T1.7), M3 (T3.1/T3.2/T3.5/T3.6×2),
M4 (13 entries, 2026-07-12), M5 (10 entries: T5.1/T5.5/T5.8-*, 2026-07-13),
and M6 (1 entry: T6.5, 2026-07-14).

**Open questions: 2** (M7 in progress — logged 2026-07-14, resolve or
archive at M7 close).

## T7.1 — `composes_touched_facet` predicate is undefined in §4.9
- **Where:** `src/akasha/tms/invalidate.py` (`_composes_touched_facet`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** §4.9's pseudocode calls `composes_touched_facet(edge, touched)` but never defines it. The predicate's first two clauses (`facet_binding in touched` / `facet_binding == '*'`) already cover every `composes` edge with a specific or wildcard binding, so this third clause can only add coverage for a `composes` edge with `facet_binding IS NULL` (a whole-node composition, no facet binding). Implemented as: such a whole-node `composes` edge is touched by ANY non-empty `touched` set — any interface break on the target is relevant to a plain "this node composes that node" subscription, regardless of which facet broke. Covered by a unit test (fires on non-empty touched, silent on empty).
- **Resolution:** open.

## T7.7 — `Facet.name` for facets-from-spans-minted facets
- **Where:** `src/akasha/kernel/store.py` (`mint_facet_from_span`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** §4.2's `Facet.name` is a "short label, unique per node," but the facets-from-spans capture flow (§T7.7) supplies only `facet_span` (the highlighted text) — no name. Implemented `name = facet_id` (the id8 is unique by construction; the span text is neither guaranteed unique nor a short label). The mint commit uses `change_class="minor"` (a brand-new v1 facet is neither removed/renamed nor version-bumped, so §4.9's heuristic gives non-major — NOT a spec question, just noted).
- **Resolution:** open.
