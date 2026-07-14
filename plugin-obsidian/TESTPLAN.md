# Obsidian plugin manual test plan (M6 / T6.2–T6.5)

Operator script for a real Obsidian demo vault with the local `tm-daemon`
running. This is **not** an automated suite — walk each step, check the
expected result, then tick the box.

Cross-references:

- Battery **E04** / **E05** / **E08** live in `tests/battery/test_edit_battery.py`
- Golden fixtures: `tests/golden/reconcile/e04-cross-file-move/`,
  `tests/golden/reconcile/e04b-s0-cross-file-move/`,
  `tests/golden/reconcile/e05-cross-file-dup/`,
  `tests/golden/reconcile/create-tm-new/` (E08)

---

## 0. Setup

1. [ ] Build the plugin from the repo root:

    ```bash
    cd plugin-obsidian && npm ci && npm run build
    ```

    Expected: exit 0; `plugin-obsidian/main.js` exists.

2. [ ] Install or symlink the plugin into a demo Obsidian vault’s community
   plugins folder as `tm-hub` (plugin id from `manifest.json`):

    ```bash
    # Example — adjust VAULT to your demo vault path
    VAULT="$HOME/Obsidian/DemoVault"
    mkdir -p "$VAULT/.obsidian/plugins"
    ln -sfn "$(pwd)/plugin-obsidian" "$VAULT/.obsidian/plugins/tm-hub"
    # Ensure main.js + manifest.json are visible under that path
    ```

    Expected: `$VAULT/.obsidian/plugins/tm-hub/manifest.json` and `main.js`
    resolve.

3. [ ] Start the tm-daemon (default bind `http://127.0.0.1:7433`):

    ```bash
    # from the akasha repo root
    uv run akasha daemon
    # or: akasha daemon
    ```

    Expected: process stays up; `curl -s http://127.0.0.1:7433/health`
    returns OK JSON (or equivalent health check succeeds).

4. [ ] Ensure the demo vault is registered as a sync root with the daemon
   (CLI or API per your local setup) so file saves are watched and
   reconciled.

5. [ ] Open the demo vault in Obsidian → Settings → Community plugins →
   enable **TM Hub** (and “Safe mode” off if required).

    Expected: plugin loads with no uncaught exception in
    Developer Tools → Console.

6. [ ] Open (or create) at least two **managed** notes under the sync root
   that already carry front-matter `tm: 1` and at least one line ending in a
   minted `^tm-<id8>` anchor (8 chars from `abcdefghijklmnopqrstuvwxyz234567`).
   Call them **file A** and **file B** below.

---

## 1. T6.2 — Settings persistence

1. [ ] Open Settings → TM Hub.

2. [ ] Set **Daemon URL** to `http://127.0.0.1:7433` (or your running
   daemon’s base URL).

3. [ ] Set **API token** to a valid Bearer token for that daemon.

    Expected: fields accept input; no crash.

4. [ ] Quit Obsidian completely (full process exit, not just close the
   window on some platforms) and reopen the same vault.

5. [ ] Reopen Settings → TM Hub.

    Expected: Daemon URL and API token are still the values you entered
    (token field may show masked characters, but the stored value matches).

---

## 2. T6.3 — Status bar

1. [ ] With the daemon running and settings correct, look at the Obsidian
   status bar (bottom).

    Expected: text in the style of `TM: synced · 0 violations` (or
    `TM: N violations` if the review/violation queue is non-empty). A sync
    state and a violation count are both conveyed.

2. [ ] Open Developer Tools → Console; leave it open.

3. [ ] Stop the daemon process (Ctrl+C / kill). Do **not** reload Obsidian.

4. [ ] Wait ~5–10 seconds (status bar polls on a ~5s interval).

    Expected: status bar degrades to an offline-style state such as
    `TM: offline`. No uncaught exception / red error spam in the console
    from the plugin’s poll loop.

5. [ ] Restart the daemon; within another poll interval the status bar
   should leave the offline state again (optional sanity check).

---

## 3. T6.4 — Create node from selection

This path must match battery **E08** (`create-tm-new` / no echo loop): the
plugin only appends `^tm-new`; the daemon mints and rewrites once.

1. [ ] In a managed note, place the cursor on a line of plain text that does
   **not** already end with a `^tm-…` anchor (blank-line edge: prefer a
   non-empty prose line).

2. [ ] Select some or all of that line’s text (or leave a collapsed cursor
   on the line).

3. [ ] Run the command palette command **Create node from selection**
   (`tm-create-node-from-selection`).

    Expected: that line now ends with a trailing ` ^tm-new` suffix. The
    plugin does **not** invent an `id8` itself.

4. [ ] Save the file (Obsidian auto-save or explicit save).

5. [ ] Wait for the daemon watcher/reconcile cycle (usually a few seconds).

    Expected: the daemon rewrites `^tm-new` to a minted `^tm-<id8>` where
    `<id8>` is exactly 8 characters from `abcdefghijklmnopqrstuvwxyz234567`.

6. [ ] Watch the file for further thrashing.

    Expected: the file stabilizes after **exactly one** daemon-initiated
    rewrite — no further edit/echo loop (battery **E08** guarantee).

---

## 4. T6.5 — Clipboard cut/copy anchor semantics

Anchors are plain markdown. Native cut/copy must retain the trailing
`^tm-<id8>` text. After both files are saved, the daemon’s three-way
reconcile decides MOVE vs duplicate.

### 4a. CUT scenario (cross-file move)

This corresponds to battery test **E04** (golden/reconcile/e04-cross-file-move/,
and the S0 companion e04b-s0-cross-file-move/).

1. [ ] In **file A**, select an entire managed line/block that already ends
   with a minted `^tm-<id8>` (note the exact anchor string, e.g.
   `^tm-6mvyqsqb`).

2. [ ] Cut it (Ctrl+X / Cmd+X).

    Expected: the line (and its trailing anchor) leave file A’s buffer; the
    clipboard still contains the full line text including `^tm-<id8>`.

3. [ ] Paste into **file B** (Ctrl+V / Cmd+V) at a sensible location.

    Expected: file B now shows the same line with the **identical** anchor.

4. [ ] Save **both** file A and file B; wait for reconcile.

    Expected:

    - File A no longer contains that anchor (line gone, or no longer carries
      that `^tm-<id8>`).
    - File B contains the line with the **same** `^tm-<id8>`.
    - Daemon treats this as a clean **MOVE** of one node — no
      duplicate / `E_DUP_ID` review raised for that anchor.

### 4b. COPY scenario (cross-file duplicate)

This corresponds to battery test **E05** (golden/reconcile/e05-cross-file-dup/).

1. [ ] Restore a known-good state if needed (or use a different minted line):
   file A has a line ending in `^tm-<id8>`; file B does not yet have that
   same anchor.

2. [ ] In file A, select the full anchored line and **copy** it (Ctrl+C /
   Cmd+C) — do **not** cut; leave the original line untouched.

    Expected: file A still has the original line + anchor; clipboard holds
    the same text including `^tm-<id8>`.

3. [ ] Paste into file B; save **both** files; wait for reconcile.

    Expected:

    - File A still has the original anchor line.
    - File B now **also** has a line with the **identical** `^tm-<id8>`
      (two live copies of the same id across files).
    - Daemon surfaces a review-queue violation with cause code **`E_DUP_ID`**
      (`cause_kind` conflict/violation). Status bar violation count may
      increase accordingly.

---

## Pass criteria

- [ ] All T6.2 steps: settings persist across full reload.
- [ ] All T6.3 steps: live sync/violation text while online; clean `offline`
  degrade with daemon stopped.
- [ ] All T6.4 steps: `^tm-new` → single minted rewrite, no echo (E08).
- [ ] All T6.5 cut steps: clean MOVE (E04 / e04b).
- [ ] All T6.5 copy steps: `E_DUP_ID` review (E05).
