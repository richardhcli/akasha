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

    logging.getLogger("akasha").handlers.clear()
