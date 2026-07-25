"""Real cross-process single-instance-lock test (T4.9 follow-up, 2026-07-24).

``test_daemon_lock.py`` covers the lock primitive same-process: two
``single_instance_lock()`` calls made by ONE Python process (one PID, two
file handles). That is a real test of Windows' per-handle byte-range
locking, but it leaves one gap: the actual real-world failure mode this
lock exists to prevent is a **second OS process** -- a second ``akasha
daemon`` launch while one is already running -- which same-process testing
cannot fully rule out (e.g. any hypothetical same-process-handle-sharing
quirk would be invisible to it).

This file closes that gap by spawning two genuinely separate OS processes
(via ``subprocess`` against the project's own venv interpreter,
``sys.executable``) that race for the same lock file. It deliberately does
NOT spin up full ``uvicorn`` daemons -- ``test_daemon_lock.py``'s own
docstring already ruled that out as flaky for this kind of test; only the
lock primitive itself (the actual contended resource) is exercised, via
``_lock_subprocess_helper.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_HELPER = Path(__file__).resolve().parent / "_lock_subprocess_helper.py"


def test_second_real_process_fails_while_first_holds_the_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "tm-daemon.lock"

    holder = subprocess.Popen(
        [sys.executable, str(_HELPER), str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        line = holder.stdout.readline()  # type: ignore[union-attr]
        assert line.strip() == "ACQUIRED", (
            f"holder process did not report acquiring the lock: {line!r}"
        )

        challenger = subprocess.run(
            [sys.executable, str(_HELPER), str(lock_path)],
            input="unused\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert challenger.returncode == 4, (
            f"a second real OS process racing an already-held lock must exit 4 "
            f"(spec §4.12 conflict class), got {challenger.returncode}; "
            f"stdout={challenger.stdout!r} stderr={challenger.stderr!r}"
        )
        assert challenger.stdout.strip() == f"ALREADY_RUNNING:{lock_path}"
    finally:
        holder.stdin.write("release\n")  # type: ignore[union-attr]
        holder.stdin.flush()  # type: ignore[union-attr]
        holder.wait(timeout=10)

    assert holder.returncode == 0


def test_lock_available_again_after_holder_process_exits(tmp_path: Path) -> None:
    lock_path = tmp_path / "tm-daemon.lock"

    holder = subprocess.Popen(
        [sys.executable, str(_HELPER), str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    line = holder.stdout.readline()  # type: ignore[union-attr]
    assert line.strip() == "ACQUIRED"
    holder.stdin.write("release\n")  # type: ignore[union-attr]
    holder.stdin.flush()  # type: ignore[union-attr]
    holder.wait(timeout=10)
    assert holder.returncode == 0

    # A brand new real process must be able to acquire the now-freed lock --
    # proves release (not just acquisition) is genuinely cross-process.
    second = subprocess.run(
        [sys.executable, str(_HELPER), str(lock_path)],
        input="unused\n",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert second.returncode == 0
    assert second.stdout.strip() == "ACQUIRED"
