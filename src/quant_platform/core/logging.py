"""Structured logging via structlog. Secrets are filtered out of every record."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import structlog

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)\S+"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1***"),
]


def _redact_secrets(_logger: object, _method: str, event_dict: dict) -> dict:
    for key, value in list(event_dict.items()):
        if any(s in key.lower() for s in ("api_key", "secret", "token", "password")):
            event_dict[key] = "***"
        elif isinstance(value, str):
            redacted = value
            for pat, repl in _SECRET_PATTERNS:
                redacted = pat.sub(repl, redacted)
            event_dict[key] = redacted
    return event_dict


def configure_logging(log_dir: Path | str = "logs", level: str = "INFO") -> None:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path / "platform.log", encoding="utf-8")
    file_handler.setLevel(level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)

    logging.basicConfig(level=level, handlers=[file_handler, console_handler], format="%(message)s")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
