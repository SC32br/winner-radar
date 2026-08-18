"""Логи сбора: в каждой строке можно передать external_id."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app import config

_configured = False


class _ExternalIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "external_id"):
            record.external_id = "-"
        return True


def setup_logging(name: str = "radar") -> logging.Logger:
    global _configured
    log = logging.getLogger(name)
    if _configured:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s external_id=%(external_id)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        config.LOG_DIR / "collect.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(_ExternalIdFilter())
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(_ExternalIdFilter())
    log.addHandler(file_handler)
    log.addHandler(stream)
    log.propagate = False
    _configured = True
    return log


class LotLog(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("external_id", self.extra.get("external_id", "-"))
        return msg, kwargs
