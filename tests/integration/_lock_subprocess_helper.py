"""Standalone child-process helper for cross-process single-instance-lock
tests (``test_daemon_lock_multiprocess.py``). Not a pytest test file itself
(leading underscore keeps pytest from collecting it) -- this script's only
job is to run in its own real OS process, attempt to acquire the lock, and
report the outcome over stdout so the parent test process can assert on it.

Usage: ``python _lock_subprocess_helper.py <lock_path>``. On successful
acquisition, prints ``ACQUIRED`` and then blocks reading one line from
stdin (the parent's release signal) before releasing the lock and exiting
0. On ``AlreadyRunningError``, prints ``ALREADY_RUNNING:<lock_path>`` and
exits 4 (mirrors the CLI's own exit-code mapping, spec §4.12).
"""

from __future__ import annotations

import sys
from pathlib import Path

from akasha import daemon as daemon_module


def main() -> int:
    lock_path = Path(sys.argv[1])
    try:
        with daemon_module.single_instance_lock(lock_path):
            print("ACQUIRED", flush=True)
            sys.stdin.readline()  # block until the parent test signals release
            return 0
    except daemon_module.AlreadyRunningError as exc:
        print(f"ALREADY_RUNNING:{exc.lock_path}", flush=True)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
