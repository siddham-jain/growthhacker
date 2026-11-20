import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

import structlog
from pydantic import SecretStr

from app.core.config import AppSettings

REDACTED = "[redacted]"
SECRET_KEY_PATTERN = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|refresh_token|access_token|credential|pepper)",
    re.IGNORECASE,
)
VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"\bghk_[A-Za-z0-9_]+\b"), "[api_key]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[api_key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "[api_key]"),
    (re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b"), "[email]"),
    (re.compile(r"(?<![\w])\+?\d[\d\s().-]{8,}\d(?![\w])"), "[phone]"),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"), r"\1[redacted]@"),
    (
        re.compile(
            r"(?i)([?&](?:key|api[_-]?key|access_token|refresh_token|token|client_secret|code|signature|sig)=)"
            r"[^&#\s'\"]+"
        ),
        r"\1[redacted]",
    ),
)
# these log full request urls at info, query strings included
NOISY_LOGGERS = ("httpx", "httpcore")


def redact_text(value: str) -> str:
    for pattern, replacement in VALUE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact_value(key: str | None, value: Any) -> Any:
    if isinstance(value, SecretStr):
        return REDACTED
    if key is not None and SECRET_KEY_PATTERN.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {item_key: redact_value(str(item_key), item) for item_key, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(None, item) for item in value]
    return value


def redaction_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value if key in {"timestamp", "level", "logger"} else redact_value(key, value)
        for key, value in event_dict.items()
    }


class RedactingFilter(logging.Filter):
    """stdlib records (httpx, uvicorn, libraries) get the same value redaction as structlog events."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))
        return True


def install_stdlib_redaction() -> None:
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # uvicorn and others attach handlers to non-propagating loggers, so every handler gets the filter
    loggers = [logging.getLogger(), *logging.root.manager.loggerDict.values()]
    for logger in loggers:
        for handler in getattr(logger, "handlers", []):
            if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
                handler.addFilter(RedactingFilter())


def configure_logging(settings: AppSettings) -> None:
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            redaction_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    install_stdlib_redaction()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
