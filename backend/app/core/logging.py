"""Logging setup. Kept dependency-free for the skeleton; structured logging can
be layered on later without changing call sites."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import settings

_CONFIGURED = False
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
# The packaged app runs this backend with its console going nowhere, so a
# customer-side failure (e.g. Lucy GO LIVE) used to leave no trace at all.
# Keep a small rolling log next to the app's data instead: data/logs/backend.log.
LOG_FILE = Path("data") / "logs" / "backend.log"


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=_FORMAT,
    )
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(logging.Formatter(_FORMAT))
        logging.getLogger().addHandler(fh)
    except Exception:
        pass   # logging to a file is a nice-to-have; never block startup on it
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
