# Web UI

Daemon-served, no build step (static HTML shells + vanilla JS calling `/v1` — `docs/mvp-spec.md` §4.13). Code-complete, locally browser-verified (see M8 in [`../agents/task-status.md`](../agents/task-status.md)).

1. Start the daemon and get a token ([`quickstart.md`](quickstart.md)).
2. Open `http://127.0.0.1:7433/` in a browser.
3. On first load, set your bearer token: open the browser console and run `localStorage.setItem('tm_token', '<your token>')`, then reload. (There is no login screen yet — this is the only way to authenticate the UI today.)

## Views

| Path | Purpose |
|---|---|
| `/node?id=<id>` | body, facets, 1-hop neighborhood, history, stale badge |
| `/review` | open review queue, one-click resolutions, daily-cap banner |
| `/search` | full-text search over node bodies |
| `/sync` | per-sync-root status, violations, pause-and-diff inspector |

Badge copy always reads "vetted by you" / "stale — needs recheck", never "true" (PRD R9) — the UI never asserts truth on your behalf.
