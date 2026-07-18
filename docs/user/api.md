# HTTP API

The localhost API is the single interface every surface (CLI, web UI, Obsidian plugin) speaks — `docs/mvp-spec.md` §1. Base URL defaults to `http://127.0.0.1:7433`, bound to loopback only.

**Endpoint table, request/response shapes, and error envelope are specified once in [`../mvp-spec.md`](../mvp-spec.md) §4.11 — this page does not repeat that table.** Two equivalent authoritative references once the daemon is running:

- Interactive docs: `http://127.0.0.1:7433/docs` (FastAPI auto-generated)
- Frozen contract: [`../api-snapshot/openapi.json`](../api-snapshot/openapi.json) — this is what the CI migration gate diffs against; it is always in sync with the served spec.

## Auth

Every endpoint except `GET /health` requires `Authorization: Bearer <token>`. Two token classes:

- **human** — mutates directly.
- **agent** — every mutating call is rewritten into a review-queue proposal (HTTP 202) instead of applying; empty-marked (`∅`) endpoints (`/tokens`, `/sync/roots`, `/nodes/{id}/vet`) reject agent tokens outright with `403 E_HUMAN_ONLY`.

Getting your first token: [`quickstart.md`](quickstart.md) step 2.

## Example

```bash
curl -s http://127.0.0.1:7433/health

curl -s -X POST http://127.0.0.1:7433/v1/nodes \
  -H "Authorization: Bearer $AKASHA_TOKEN" -H 'Content-Type: application/json' \
  -d '{"node_type":"claim","body":"caffeine impairs sleep"}'
```

Errors are always `{"error": {"code": "...", "message": "...", "detail": {}}}`.
