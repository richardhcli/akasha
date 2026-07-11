"""Process lifecycle, single-instance lock (later milestones).

For now this module only carries structured JSON logging setup (task T0.6).
"""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        return json.dumps(payload)


def configure_logging(log_file: str | Path, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("akasha")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = JsonLineFormatter()

    file_handler = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger
