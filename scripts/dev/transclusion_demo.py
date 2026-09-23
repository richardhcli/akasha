#!/usr/bin/env python3
"""Dev-only playground: build mirrored (transcluded) markdown files from scratch, watch them sync.

NOT a build-plan task -- dev tooling in the same class as ``seed_and_run.py``
(see scripts/README.md). It documents and exercises
the M19 "Mirrors" rule (docs/mvp-spec.md §4.7): the SAME ``^tm-id`` anchor in
several files is one node shown more than once, so editing the line in one
file rewrites it in every other file, as soon as the daemon's watcher
(500 ms debounce) sees the change.

Unlike ``seed_and_run.py`` this runs the REAL daemon (``akasha daemon``, i.e.
``daemon.serve`` -- the watcher, reconciler and startup reconcile), because
serving ``create_app`` alone starts no file watcher and so shows no
file-to-file propagation. Everything lives in one scratch directory (its own
``config.toml``, DB, lock file, vault, and token), so it never touches your
real ``~/.config/tm-daemon`` and can run beside a real daemon.

Usage:
    uv run python scripts/dev/transclusion_demo.py             # set up, print cheat-sheet, stay up
    uv run python scripts/dev/transclusion_demo.py --selftest  # set up, PROVE it, tear down (0/1)
    uv run python scripts/dev/transclusion_demo.py --dir ./play  # use (and keep) your own new dir
(also: ``make demo-transclusion`` / ``make demo-transclusion-check``)

Interactive mode leaves ``<dir>/vault/{A,B,C}.md`` plus an ``./ak`` wrapper
(the akasha CLI, already pointed at this daemon with its token) and prints
what to try. Ctrl-C stops the daemon. Requires the mirror support in
``sync/reconcile.py`` (M19); on a tree without it setup fails with a violation
(the daemon refuses the same anchor in two files) and exits 1.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

_ANCHOR_RE = re.compile(r"\^tm-([0-9a-z]{8})\b")
_STARTUP_TIMEOUT = 20.0
_SETTLE_TIMEOUT = 8.0
_LATENCY_CEILING = 4.0
_NEGATIVE_WAIT = 1.6  # > the 500 ms debounce, for "this must NOT propagate" checks

_TASK_TEXT = "- [ ] ship the transclusion demo"
_NOTE_TEXT = "Caffeine has a half-life of about five hours."

_A_MD = f"""---
tm: 1
---
# File A

{_TASK_TEXT} ^tm-new
{_NOTE_TEXT} ^tm-new
"""

# B and C carry the SAME anchored lines inside different surrounding prose.
_B_MD = """---
tm: 1
---
# File B (mirrors A)

Some unrelated text that is only in B.

{task}
{note}
"""

_C_MD = """---
tm: 1
---
# File C (mirrors only the note)

{note}

More unrelated text that is only in C.
"""


class DemoError(RuntimeError):
    pass


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _wait_until(predicate, timeout: float, interval: float = 0.02) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _ids_in(text: str) -> list[str]:
    return _ANCHOR_RE.findall(text)


class Demo:
    """One scratch daemon + vault. ``start()`` builds it; ``stop()`` tears the daemon down."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.vault = root / "vault"
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.token = ""
        self.proc: subprocess.Popen[bytes] | None = None
        self.task_id = ""
        self.note_id = ""

    # -- paths ---------------------------------------------------------
    @property
    def a(self) -> Path:
        return self.vault / "A.md"

    @property
    def b(self) -> Path:
        return self.vault / "B.md"

    @property
    def c(self) -> Path:
        return self.vault / "C.md"

    # -- plumbing --------------------------------------------------------
    def _cli(self, *args: str, token: bool = False) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, "-m", "akasha.cli.main"]
        if token:
            cmd += ["--base-url", self.base_url, "--token", self.token]
        return subprocess.run(  # noqa: S603
            [*cmd, *args], capture_output=True, text=True, check=False
        )

    def _api(self, method: str, path: str, **kw: object) -> httpx.Response:
        return httpx.request(
            method,
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10.0,
            **kw,  # type: ignore[arg-type]
        )

    # -- setup -----------------------------------------------------------
    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if any(self.root.iterdir()):
            raise DemoError(
                f"{self.root} is not empty -- pass a fresh --dir (a reused store is what makes "
                "`akasha init` exit 4 and leaves you without a token)"
            )
        self.vault.mkdir()
        config = self.root / "config.toml"
        config.write_text(
            f'port = {self.port}\ndb_path = "{(self.root / "store.db").as_posix()}"\n',
            encoding="utf-8",
        )

        init = self._cli("init", "--config", str(config), "--name", "demo")
        if init.returncode != 0 or not init.stdout.strip():
            raise DemoError(f"akasha init failed ({init.returncode}): {init.stderr.strip()}")
        self.token = init.stdout.splitlines()[0].strip()
        (self.root / "token.txt").write_text(self.token + "\n", encoding="utf-8")
        self._write_wrapper()

        self.proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "akasha.cli.main", "daemon", "--config", str(config)],
            stdout=(self.root / "daemon.out").open("wb"),
            stderr=subprocess.STDOUT,
        )
        (self.root / "daemon.pid").write_text(f"{self.proc.pid}\n", encoding="utf-8")
        if not _wait_until(self._daemon_up, _STARTUP_TIMEOUT, 0.1):
            raise DemoError(f"daemon did not answer on {self.base_url}; see {self.root}/daemon.out")

        # 1. A.md with two ^tm-new anchors, written BEFORE the root is registered (the watcher only
        #    picks a new root up on its next poll tick, so an early write would be missed); the
        #    rescan then discovers it and the daemon mints real ids into the file.
        self.a.write_text(_A_MD, encoding="utf-8")
        added = self._cli("sync", "add", str(self.vault), "--name", "demo", token=True)
        if added.returncode != 0:
            raise DemoError(
                f"akasha sync add failed: {added.stderr.strip() or added.stdout.strip()}"
            )
        self._api("POST", "/v1/sync/rescan")
        if not _wait_until(lambda: len(_ids_in(_read(self.a))) == 2, _SETTLE_TIMEOUT):
            raise DemoError(f"daemon never minted ids into A.md (still: {_read(self.a)!r})")
        time.sleep(1.5)  # let the live watcher schedule the new root before B/C are written
        self.task_id, self.note_id = _ids_in(_read(self.a))

        # 2. B.md / C.md carry the SAME anchored lines = mirrors (same anchor => same node).
        task_line = next(x for x in _read(self.a).splitlines() if self.task_id in x)
        note_line = next(x for x in _read(self.a).splitlines() if self.note_id in x)
        self.b.write_text(_B_MD.format(task=task_line, note=note_line), encoding="utf-8")
        self.c.write_text(_C_MD.format(note=note_line), encoding="utf-8")
        self._settle()

    def _daemon_up(self) -> bool:
        try:
            return (
                httpx.get(
                    f"{self.base_url}/v1/sync/status",
                    timeout=1.0,
                    headers={"Authorization": f"Bearer {self.token}"},
                ).status_code
                == 200
            )
        except httpx.HTTPError:
            return False

    def _settle(self) -> None:
        """Wait until the hub has adopted A/B/C with no violation (else no mirror formed)."""

        def adopted() -> bool:
            root = self._api("GET", "/v1/sync/status").json()["sync_roots"][0]
            names = {Path(f["path"]).name for f in root["files"]}
            return {"A.md", "B.md", "C.md"} <= names

        if not _wait_until(adopted, _SETTLE_TIMEOUT):
            raise DemoError("hub never adopted A.md, B.md and C.md")
        time.sleep(1.0)  # let any adoption echo (and any bogus violation) surface
        root = self._api("GET", "/v1/sync/status").json()["sync_roots"][0]
        if root["violations"]:
            codes = [v.get("cause_ref", "") for v in root["violations"]]
            raise DemoError(
                "the daemon rejected the mirrored files (does this tree lack the M19 mirror "
                f"support in sync/reconcile.py): {codes}"
            )
        # the mirrors must still be byte-identical to A's lines
        if self.task_id not in _read(self.b) or self.note_id not in _read(self.c):
            raise DemoError("B.md/C.md lost their mirrored anchors during adoption")

    def _write_wrapper(self) -> None:
        sh = self.root / "ak"
        sh.write_text(
            "#!/bin/sh\n"
            f'exec "{sys.executable}" -m akasha.cli.main --base-url {self.base_url} '
            f'--token "$(cat "{self.root / "token.txt"}")" "$@"\n',
            encoding="utf-8",
        )
        sh.chmod(0o755)
        (self.root / "ak.cmd").write_text(
            f'@echo off\r\nset /p TM_TOKEN=<"{self.root / "token.txt"}"\r\n'
            f'"{sys.executable}" -m akasha.cli.main --base-url {self.base_url} '
            f'--token "%TM_TOKEN%" %*\r\n',
            encoding="utf-8",
        )

    # -- teardown ---------------------------------------------------------
    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            if os.name == "nt":
                self.proc.terminate()
            else:
                self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=8)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):  # a second Ctrl-C: force it
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None

    # -- the proof ----------------------------------------------------------
    def selftest(self) -> list[tuple[str, bool, str]]:
        results: list[tuple[str, bool, str]] = []

        def record(name: str, ok: bool, detail: str) -> None:
            results.append((name, ok, detail))
            print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({detail})", flush=True)

        def propagate(name: str, write: Path, transform, watch: list[Path], needle: str) -> None:  # noqa: ANN001
            write.write_text(transform(_read(write)), encoding="utf-8")
            t0 = time.monotonic()
            ok = _wait_until(lambda: all(needle in _read(p) for p in watch), _LATENCY_CEILING)
            record(
                name,
                ok,
                f"{time.monotonic() - t0:.2f}s" if ok else f"not seen within {_LATENCY_CEILING}s",
            )

        # 1. edit the note in A -> B and C follow
        propagate(
            "edit A.md's note -> B.md and C.md change",
            self.a,
            lambda t: t.replace("about five hours", "roughly 5.7 hours (edited in A)"),
            [self.b, self.c],
            "edited in A",
        )
        # 2. edit in the OTHER direction (C -> A, B)
        propagate(
            "edit C.md's note -> A.md and B.md change",
            self.c,
            lambda t: t.replace("(edited in A)", "(edited in C)"),
            [self.a, self.b],
            "edited in C",
        )
        # 3. tick the checkbox in B -> A follows
        propagate(
            "tick the checkbox in B.md -> A.md follows",
            self.b,
            lambda t: t.replace("- [ ] ship", "- [x] ship"),
            [self.a],
            "- [x] ship",
        )
        # 4. hub-side edit (CLI) rewrites every file
        r = self._cli("set", self.note_id, "--body", "Set from the hub via akasha set.", token=True)
        if r.returncode != 0:
            record(
                "hub-side `akasha set` lands in every file",
                False,
                r.stderr.strip() or r.stdout.strip(),
            )
        else:
            t0 = time.monotonic()
            ok = _wait_until(
                lambda: all("Set from the hub" in _read(p) for p in (self.a, self.b, self.c)),
                _LATENCY_CEILING,
            )
            record(
                "hub-side `akasha set` lands in A.md, B.md and C.md",
                ok,
                f"{time.monotonic() - t0:.2f}s" if ok else "not seen",
            )
        # 5. deleting one copy leaves the node and the other copies alone
        self.c.write_text(
            _read(self.c).replace(
                next(x for x in _read(self.c).splitlines() if self.note_id in x) + "\n", ""
            ),
            encoding="utf-8",
        )
        time.sleep(_NEGATIVE_WAIT)
        still = all(self.note_id in _read(p) for p in (self.a, self.b))
        record(
            "removing the line from C.md leaves A.md and B.md intact",
            still,
            "kept" if still else "lost",
        )
        # 6. a pasted copy with DIFFERENT text: the hub's text wins, your version goes to review
        conflicts_before = self._open_conflicts()
        d = self.vault / "D.md"
        d.write_text(
            f"---\ntm: 1\n---\nMy own wording of the note. ^tm-{self.note_id}\n", encoding="utf-8"
        )
        healed = _wait_until(lambda: "Set from the hub" in _read(d), _SETTLE_TIMEOUT)
        time.sleep(0.5)
        added = self._open_conflicts() - conflicts_before
        record(
            "a differing copy in D.md is rewritten to the hub's text, one conflict review kept",
            healed and added == 1,
            f"rewritten={healed}, new conflict reviews={added}",
        )
        # 7. detach: give B's copy ^tm-new -> it becomes its own node; A no longer drives it
        b_text = _read(self.b)
        self.b.write_text(b_text.replace(f"^tm-{self.note_id}", "^tm-new"), encoding="utf-8")
        ok = _wait_until(lambda: "^tm-new" not in _read(self.b), _SETTLE_TIMEOUT)
        new_ids = [i for i in _ids_in(_read(self.b)) if i != self.task_id]
        detached = ok and bool(new_ids) and new_ids[0] != self.note_id
        record(
            "^tm-new in B.md detaches it (gets its own id)",
            detached,
            new_ids[0] if new_ids else "no id",
        )
        if detached:
            before = _read(self.b)
            self.a.write_text(
                _read(self.a).replace("Set from the hub via akasha set.", "A alone."),
                encoding="utf-8",
            )
            time.sleep(_NEGATIVE_WAIT)
            record(
                "after detaching, editing A.md no longer changes B.md",
                _read(self.b) == before,
                "unchanged" if _read(self.b) == before else "B changed",
            )
        return results

    def _open_conflicts(self) -> int:
        reviews = self._api("GET", "/v1/review").json()["reviews"]
        return sum(1 for r in reviews if r.get("cause_kind") == "conflict")

    # -- interactive ------------------------------------------------------
    def cheat_sheet(self) -> str:
        v = self.vault
        return f"""
Transclusion playground is up  (real daemon on {self.base_url}, scratch dir {self.root})

  {v}/A.md   the line "{_TASK_TEXT}" and the caffeine note
  {v}/B.md   mirrors BOTH lines, inside different text
  {v}/C.md   mirrors only the caffeine note

Every ^tm-id anchor that appears in more than one file is ONE node. Try:

  1. Open A.md and B.md side by side in any editor. Change the caffeine sentence in
     either file and save: the other files change within ~0.5-1 s (the monitor below
     prints each rewrite as it lands).
  2. Tick the checkbox in B.md ("- [ ]" -> "- [x]"): A.md follows.
  3. From the hub:      {self.root}/ak set {self.note_id} --body "written from the CLI"
                        {self.root}/ak get {self.note_id}
  4. Make your own mirror: add a line to A.md ending in ^tm-new, save (the daemon
     mints the id), then copy that whole line, id included, into another .md file in
     {v}/ .  One-line blocks only (a paragraph line or a task line).
  5. Detach a copy: change its ^tm-{self.note_id} to ^tm-new; it becomes its own node.
  6. Paste a copy of an anchored line into a new file but with DIFFERENT text: the file is
     rewritten to the hub's text and your wording is kept as a conflict review
     ({self.root}/ak review list). (Editing two files in the same instant is resolved the
     same way: one version wins, the other is kept for review.)

Stop: Ctrl-C here, or  kill $(cat {self.root}/daemon.pid)
Logs: {self.root}/daemon.out  and  {self.root}/tm-daemon.log
Live monitor (changes to any vault file, as the daemon rewrites them):
"""

    def monitor(self) -> None:
        seen = {p: _read(p) for p in (self.a, self.b, self.c)}
        stamp = {p: p.stat().st_mtime_ns for p in seen if p.exists()}
        while self.proc is not None and self.proc.poll() is None:
            time.sleep(0.1)
            for p in sorted(self.vault.glob("*.md")):
                try:
                    m = p.stat().st_mtime_ns
                except FileNotFoundError:
                    continue
                if stamp.get(p) == m:
                    continue
                stamp[p] = m
                new = _read(p)
                old = seen.get(p, "")
                seen[p] = new
                changed = [ln for ln in new.splitlines() if ln not in old.splitlines()]
                for ln in changed:
                    print(f"  [{time.strftime('%H:%M:%S')}] {p.name}: {ln}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--selftest", action="store_true", help="prove mirroring end to end, tear down, exit 0/1"
    )
    ap.add_argument(
        "--dir", type=Path, default=None, help="scratch directory (must be empty or new; kept)"
    )
    args = ap.parse_args()

    made_tmp = args.dir is None
    passed = False
    root = Path(tempfile.mkdtemp(prefix="tm-transclusion-")) if made_tmp else args.dir.resolve()
    demo = Demo(root)
    try:
        try:
            print(f"setting up in {root} ...", flush=True)
            demo.start()
        except DemoError as exc:
            print(f"SETUP FAILED: {exc}", file=sys.stderr)
            return 1

        if args.selftest:
            print("mirror self-test (real daemon, real files):", flush=True)
            results = demo.selftest()
            failed = [n for n, ok, _ in results if not ok]
            print("\nALL PASSED" if not failed else f"\nFAILED: {failed}", flush=True)
            passed = not failed
            return 1 if failed else 0

        print(demo.cheat_sheet(), flush=True)
        try:
            demo.monitor()
        except KeyboardInterrupt:
            pass
        print("\nstopping the daemon ...", flush=True)
        return 0
    finally:
        demo.stop()
        if args.selftest and made_tmp and passed:
            shutil.rmtree(root, ignore_errors=True)
        elif args.selftest:
            print(f"kept for inspection: {root} (daemon.out, tm-daemon.log)", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
