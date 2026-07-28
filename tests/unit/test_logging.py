import json
import logging

from akasha.daemon import configure_logging


def test_log_record_is_json_with_required_keys(tmp_path, capsys):
    log_file = tmp_path / "akasha.log"
    logger = configure_logging(log_file)
    logger.info("hello")

    for handler in logger.handlers:
        handler.flush()

    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["level"] == "INFO"
    assert "timestamp" in payload
    assert "traceback" not in payload

    logging.getLogger("akasha").handlers.clear()


def test_log_record_includes_traceback_on_exception(tmp_path):
    log_file = tmp_path / "akasha.log"
    logger = configure_logging(log_file)

    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("unhandled exception sampling /v1/metrics")

    for handler in logger.handlers:
        handler.flush()

    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "unhandled exception sampling /v1/metrics"
    assert "ValueError: boom" in payload["traceback"]
    assert "Traceback (most recent call last)" in payload["traceback"]

    logging.getLogger("akasha").handlers.clear()
