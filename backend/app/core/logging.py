import logging
import re
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.core.config import settings


_URL_PATTERN = re.compile(r"(?:https?|wss?)://[^\s\"']+", re.IGNORECASE)
_WINDOWS_PATH_PATTERN = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/])[^\s\"']+")
_POSIX_PATH_PATTERN = re.compile(r"(?<![\w])/(?:[^\s/]+/)+[^\s\"']+")
_SQL_PATTERN = re.compile(
    r"\b(?:SELECT\s+.+?\s+FROM|INSERT\s+INTO|UPDATE\s+.+?\s+SET|"
    r"DELETE\s+FROM|ALTER\s+TABLE|CREATE\s+TABLE|DROP\s+TABLE|PRAGMA\s+|"
    r"REPLACE\s+INTO)\b",
    re.IGNORECASE,
)


def _redact_message(message: object) -> object:
    if not isinstance(message, str):
        return message
    if _SQL_PATTERN.search(message):
        return "Log detail suppressed: SQL"
    parts = re.split(r"(%(?:\([^)]+\))?[-+#0 ]*(?:\d+|\*)?(?:\.\d+|\.\*)?[hlL]?[diouxXeEfFgGcrsa%])", message)
    for index in range(0, len(parts), 2):
        parts[index] = _URL_PATTERN.sub("<redacted>", parts[index])
        parts[index] = _WINDOWS_PATH_PATTERN.sub("<path>", parts[index])
        parts[index] = _POSIX_PATH_PATTERN.sub("<path>", parts[index])
    return "".join(parts)


def _safe_arg(arg: object) -> object:
    if isinstance(arg, BaseException):
        return type(arg).__name__
    if isinstance(arg, Path):
        return "<path>"
    if isinstance(arg, str) and _SQL_PATTERN.search(arg):
        return "<sql>"
    if isinstance(arg, str) and (
        _URL_PATTERN.search(arg)
        or PureWindowsPath(arg).is_absolute()
        or PurePosixPath(arg).is_absolute()
    ):
        return "<redacted>"
    return arg


class _ProductionSafeFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_message(record.msg)
        exc_info = record.exc_info
        if exc_info and exc_info[0] is not None:
            if isinstance(record.args, Mapping):
                record.args = {
                    key: _safe_arg(value)
                    for key, value in record.args.items()
                }
            elif record.args is not None:
                record.args = tuple(_safe_arg(arg) for arg in record.args)
            record.exc_info = None
            record.exc_text = None
        elif isinstance(record.args, Mapping):
            record.args = {
                key: _safe_arg(value)
                for key, value in record.args.items()
            }
        elif record.args:
            record.args = tuple(_safe_arg(arg) for arg in record.args)
        if exc_info:
            record.exc_info = None
            record.exc_text = None
        if (
            record.name in {"uvicorn", "uvicorn.error"}
            and isinstance(record.msg, str)
            and record.msg.startswith("Traceback (most recent call last):")
        ):
            record.msg = "Application error detail suppressed"
            record.args = ()
        return True


def _install_production_filter(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        if any(isinstance(filter_, _ProductionSafeFilter) for filter_ in handler.filters):
            continue
        handler.addFilter(_ProductionSafeFilter())


def _install_production_filters() -> None:
    for name in (None, "uvicorn", "uvicorn.access"):
        _install_production_filter(logging.getLogger(name))


def _resolve_level(name: str) -> int:
    """Map a configured level name onto its number, defaulting to INFO.

    `getLevelName` returns an int for a known name and the string
    ``"Level <name>"`` for anything else. An unrecognised value must not raise:
    app/main.py configures logging at import time, so a typo in the overlay would
    otherwise turn into a boot failure.
    """
    level = logging.getLevelName(str(name).strip().upper())
    return level if isinstance(level, int) else logging.INFO


def setup_logging() -> None:
    root = logging.getLogger()
    # Before the handler guard on purpose: the level must not depend on whether
    # something else configured a handler first.
    root.setLevel(_resolve_level(settings.LOG_LEVEL))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    production = str(settings.PMRF_ENV).strip().lower() == "production"
    if production:
        _install_production_filters()
    if root.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if settings.LOG_FILE:
        log_path = Path(settings.LOG_FILE).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if production:
        _install_production_filters()
