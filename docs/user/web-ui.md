# Web UI

Daemon-served, no build step (static HTML shells + vanilla JS calling `/v1` — `docs/mvp-spec.md` §4.13). Code-complete and browser-verified against every view (see M8 in [`../agents/task-status.md`](../agents/task-status.md); recent fixes below are logged in `../mvp-debug-plan.md`).

1. Start the daemon and get a token ([`quickstart.md`](quickstart.md)).
2. Open `http://127.0.0.1:7433/` in a browser. `/` itself is just the shell (nav + auth bar, empty otherwise) — pick a view from the nav once your token is saved.
3. Every page shows an auth bar next to the nav links. On first load it's an inline form: paste your token and click **Save token** — the page reloads and you're authenticated everywhere, no DevTools/console needed. Once set, the bar shows a masked token (`Token set (abcd…wxyz)`, the raw value is never redisplayed) plus **Change token** (re-opens the form) and **Clear token** (logs out). The token itself still lives in `localStorage.tm_token`, same as before — this is just an in-page affordance around it.
4. **Handing a freshly minted token to the browser without typing it in:** open `/<view>?token=<bearer>` (e.g. `http://127.0.0.1:7433/dashboard?token=tm-...`) and the page seeds `localStorage.tm_token` from the `token` query param on load, then immediately strips it from the visible URL/history via `history.replaceState` — it never lingers in the URL bar, browser history, or any log. This is the recommended way to move a token a CLI bootstrap command just minted into the browser (e.g. paste the printed link) without ever opening DevTools or the console.

## Views

| Path | Purpose |
|---|---|
| `/node?id=<id>` | body, facets, 1-hop neighborhood, history, stale badge |
| `/review` | open review queue; resolve with `still_holds`/`retracted`, or (for a `violation`-caused item) `dismissed`; or revise via an inline textarea + change-class selector and **submit revised**; daily-cap banner |
| `/search` | full-text search over node bodies — `/search?q=<term>` also works as a bookmarkable/shareable deep link: it hydrates the input and runs the query on load, not just on manual submit |
| `/sync` | per-sync-root status, violations, pause-and-diff inspector |
| `/dashboard` | facet coverage, review inflow vs. resolution + variance, violation rate, crossing rate — sourced live from `GET /v1/metrics` |

Any node id shown in a search result or a review-queue item (including a sync-root violation/pause/conflict entry) is a real link to that node's `/node?id=<id>` view — no manual URL editing required. Note the nav links from `/` to the other five views but not back the other way to `/` — you won't need it once you're authenticated, since every other view reads the same saved token.

Badge copy always reads "vetted by you" / "stale — needs recheck", never "true" (PRD R9) — the UI never asserts truth on your behalf.
