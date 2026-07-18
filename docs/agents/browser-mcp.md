# Browser access via Playwright MCP

This repo registers the [Playwright MCP server](https://github.com/microsoft/playwright-mcp)
at **project scope** in `.mcp.json` (checked into git), so anyone — human
or agent — working in this repo gets it automatically once they approve it.

It exists for **interactive, dev-time browser driving** while building
M8 (`docs/build-plan.md` — Web UI, tasks `T8.1`–`T8.5`): opening the daemon's
UI, clicking through a flow, taking screenshots, reading the accessibility
tree or console/network output. It is a separate thing from the
**`T8.5` CI smoke test**, which uses the `playwright` *Python* library
inside `tests/integration/test_ui_smoke.py` and runs headless in CI —
that test does not use this MCP server and should not depend on it being
configured. Don't conflate the two: this doc is about the MCP server only.

## Setup (already done)

```
claude mcp add playwright -s project -- npx @playwright/mcp@latest
```

This wrote `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "playwright": {
      "type": "stdio",
      "command": "npx",
      "args": ["@playwright/mcp@latest"],
      "env": {}
    }
  }
}
```

First run downloads the server package and a Chromium build via `npx`
(needs network access and ~700 MB disk; Node 20+).

## Approval (human users)

Project-scoped MCP servers are untrusted by default per-user, per-repo.
The first time you run `claude` in this repo after pulling `.mcp.json`,
you'll be prompted to approve or reject the `playwright` server. Approve
it once; the choice is remembered. To re-trigger the prompt (e.g. after
editing `.mcp.json`), run:

```
claude mcp reset-project-choices
```

Check status any time with `claude mcp list` / `claude mcp get playwright`.

## Using it (AI agents)

Once approved, the Playwright MCP tools appear as ordinary deferred tools
(discoverable via `ToolSearch`, e.g. `ToolSearch({query: "select:browser_navigate"})`
or a keyword search like `"browser click screenshot"`). Typical loop while
working an M8 task:

1. Start the daemon (`uv run akasha-daemon` or the project's normal dev-run
   command) so it's listening on `127.0.0.1:7433`.
2. Navigate the MCP browser to the relevant route (e.g. `http://127.0.0.1:7433/`
   for the shell, `/node/<id>` for the node view, `/review` for the queue).
3. Drive the flow under test (click, type, wait for a selector) and take a
   snapshot/screenshot to confirm the DoD in `docs/build-plan.md` for that
   task — e.g. for `T8.2`, confirm body + facets + neighborhood + history +
   stale badge all render, and that the badge text is "vetted by you", never
   "true" (per spec R9).
4. This satisfies the "test the golden path in a browser before reporting
   done" expectation from `CLAUDE.md` for UI changes — it is not a
   substitute for the automated `T8.5` Playwright test, which still needs
   to be written and passing in CI.

Do not use this server to reach any host other than the local daemon
(`127.0.0.1:7433`) unless a task explicitly calls for it — there's no
reason for an M8 task to browse the open internet.

## Using it (human users)

Run `claude` interactively in this repo, approve the server on first
prompt, and just ask in plain language, e.g.:

> Open http://127.0.0.1:7433/review in the browser and check that the
> daily-cap banner shows up once there are 10 active queue items.

Claude will call the Playwright MCP tools directly; no extra setup needed
beyond the daemon being up.

## Removing / troubleshooting

- `claude mcp remove playwright` — remove the server entirely (edits
  `.mcp.json`; commit the change if you intend it for everyone).
- If `npx @playwright/mcp@latest` fails outright, check Node version
  (`node -v`, need 20+) and that outbound network access is available for
  the first-run download.
