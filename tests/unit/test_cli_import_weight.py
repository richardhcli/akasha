"""The CLI is started once per command: importing it must not drag in the HTTP client, the store
or the API models (about 130 ms), which only a request or a bootstrap verb needs."""

from __future__ import annotations

import subprocess
import sys


def test_importing_the_cli_is_light() -> None:
    code = (
        "import sys, akasha.cli.main; "
        "names = ('httpx', 'akasha.kernel.store', 'akasha.api.auth'); "
        "heavy = [m for m in names if m in sys.modules]; "
        "print(heavy)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout
