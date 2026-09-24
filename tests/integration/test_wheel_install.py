"""T18.1 -- the built wheel is self-contained: it ships every migration.

Before this task `uv build --wheel` produced a wheel with zero `.sql` files
and `kernel/store.py` looked for `migrations/` outside `site-packages`, so no
`uv tool install`/`pipx`/`pip` route could create a database.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv is not on PATH; cannot build the wheel"
)

_PROBE = """
import sqlite3, sys
import akasha
from akasha.kernel import store

assert "site-packages" not in akasha.__file__ and sys.argv[1] in akasha.__file__, akasha.__file__
try:
    import tests  # noqa: F401
except ImportError:
    pass
else:
    raise SystemExit("repo root is importable; the probe would not prove anything")

db = sys.argv[2]
conn = store.connect(db)
store.run_migrations(conn)
names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
missing = {"nodes", "sync_roots", "tokens"} - names
print("MIGRATIONS_DIR", store.MIGRATIONS_DIR)
print("MISSING", sorted(missing))
"""


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "-o", str(out)],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    (built,) = out.glob("akasha-*.whl")
    return built


def test_wheel_contains_every_migration_byte_for_byte(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        packaged = {Path(n).name: zf.read(n) for n in zf.namelist() if n.endswith(".sql")}
        assert all(n.startswith("akasha/migrations/") for n in zf.namelist() if n.endswith(".sql"))
    repo = {p.name: p.read_bytes() for p in (REPO / "migrations").glob("*.sql")}
    assert repo, "repo has no migrations?"
    assert packaged == repo


def test_unzipped_wheel_migrates_a_fresh_db_without_the_repo(wheel: Path, tmp_path: Path) -> None:
    tree = tmp_path / "site"
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(tree)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, str(tree), str(tmp_path / "hub.db")],
        cwd=other_cwd,
        env={"PYTHONPATH": str(tree), "PATH": ""},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "MISSING []" in proc.stdout
    assert str(tree / "akasha" / "migrations") in proc.stdout
