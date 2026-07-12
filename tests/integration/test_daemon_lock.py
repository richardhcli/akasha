"""Daemon single-instance lock + autostart docs (build-plan task T4.9, spec §4.12).

Tests the lock primitive (``akasha.daemon.single_instance_lock``) directly
against a ``tmp_path`` lock file rather than spawning real ``uvicorn``
processes (flaky, per the task's testing requirement), plus one
process-free CLI-level check that a held lock maps to a clean, non-zero
exit (not a traceback) through the ``daemon`` verb, and a check that the
Windows autostart docs exist with both required sections.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from akasha import daemon as daemon_module
from akasha.cli.main import app as cli_app

runner = CliRunner()


def test_first_acquisition_succeeds(tmp_path: Path) -> None:
    lock_path = tmp_path / "tm-daemon.lock"
    with daemon_module.single_instance_lock(lock_path):
        assert lock_path.exists()


def test_second_acquisition_fails_with_clear_typed_error(tmp_path: Path) -> None:
    lock_path = tmp_path / "tm-daemon.lock"
    with daemon_module.single_instance_lock(lock_path):
        try:
            with daemon_module.single_instance_lock(lock_path):
                raise AssertionError("second acquisition should not have succeeded")
        except daemon_module.AlreadyRunningError as exc:
            # typed: not a bare OSError / generic RuntimeError leaking through
            assert isinstance(exc, daemon_module.AlreadyRunningError)
            assert exc.lock_path == lock_path
            # clear, human-readable message (not a raw errno string)
            message = str(exc)
            assert "already running" in message
            assert str(lock_path) in message


def test_release_on_clean_exit_frees_lock_for_subsequent_acquisition(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "tm-daemon.lock"
    with daemon_module.single_instance_lock(lock_path):
        pass  # clean shutdown

    # A brand new acquisition after the first was released must succeed.
    with daemon_module.single_instance_lock(lock_path):
        pass


def test_release_on_exception_still_frees_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "tm-daemon.lock"
    try:
        with daemon_module.single_instance_lock(lock_path):
            raise ValueError("boom")
    except ValueError:
        pass

    # Even after an exceptional exit, the lock must have been released.
    with daemon_module.single_instance_lock(lock_path):
        pass


def test_lock_path_parent_directory_created_if_missing(tmp_path: Path) -> None:
    lock_path = tmp_path / "nested" / "dir" / "tm-daemon.lock"
    with daemon_module.single_instance_lock(lock_path):
        assert lock_path.exists()


def test_no_product_name_in_lock_filename() -> None:
    # rule 0.6: neutral 'tm' prefix, never the product name, on disk.
    assert daemon_module.LOCK_FILE_NAME == "tm-daemon.lock"
    assert "akasha" not in daemon_module.LOCK_FILE_NAME


def test_cli_daemon_verb_exits_cleanly_and_non_zero_when_lock_held(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"  # need not exist; load_config tolerates that
    lock_path = tmp_path / daemon_module.LOCK_FILE_NAME

    with daemon_module.single_instance_lock(lock_path):
        result = runner.invoke(cli_app, ["daemon", "--config", str(config_path)])

    assert result.exit_code == 4
    assert "already running" in result.output
    assert "Traceback" not in result.output
    # AlreadyRunningError must not propagate out of the CLI as an
    # uncaught exception -- runner.invoke would otherwise re-raise it.
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_autostart_docs_file_exists_with_xml_and_nssm_sections() -> None:
    docs_path = (
        Path(__file__).resolve().parents[2] / "docs" / "autostart-windows.md"
    )
    assert docs_path.exists()
    text = docs_path.read_text(encoding="utf-8")

    # Task Scheduler XML sample.
    assert "<Task version=" in text
    assert "<LogonTrigger>" in text
    assert "akasha daemon" in text or "Arguments>daemon<" in text

    # NSSM section.
    assert "NSSM" in text
    assert "nssm install" in text
    assert "nssm start" in text
