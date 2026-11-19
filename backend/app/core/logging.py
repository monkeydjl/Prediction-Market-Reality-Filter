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


def redact_message(message: object) -> object:
    """Scrub a *message* while keeping its shape.

    Two callers share this: the log handler filter (:class:`_RedactingLogFilter`,
    installed on every deployed environment) and Sentry's
    ``before_send`` (``app/utils/sentry.py``). The second one exists because a
    log record reaches Sentry through ``Logger.callHandlers`` and carries its
    own copy of the rendered text -- so ``SENTRY_DSN`` set outside production
    used to upload the URL that the handler filter had already redacted
    (audit section 53.120). One function, so the two surfaces cannot drift
    into two different-looking messages.

    Substrings are replaced in place (``https://...`` -> ``<redacted>``) rather
    than the whole string: ``fetch failed for <redacted>`` still says what
    happened. ``%``-format specifiers are skipped so a template survives intact.
    """
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


def redact_argument(arg: object) -> object:
    """Replace one *template argument* wholesale.

    Deliberately harsher than :func:`redact_message`: an argument that is
    itself a URL has no surrounding sentence to keep, so it becomes the bare
    placeholder. Exceptions degrade to their type name -- the message can carry
    the path or the SQL fragment (section 53.107).
    """
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


# Which environments redact. `development` is deliberately absent: the whole
# cost of redaction is "you cannot see the URL you were debugging", and a local
# box pays it without getting anything back. Empty is absent for the same
# reason ``config.py`` does not reject it: ``_resolve_env_file`` reads
# ``PMRF_ENV=`` as "not set" and loads ``.env``, so this predicate has to agree
# that an empty value is development rather than a fourth, undefined case.
#
# `staging` was missing until section 53.122, and it was the wrong one to miss:
# its overlay sets ``LOG_LEVEL=INFO`` (production uses WARNING) and does not
# override ``LOG_FILE``, so it wrote *more* lines than production into the same
# ``backend/logs/app.log`` while redacting none of them. ``docs/ops/RUNBOOK.md``
# already forbids copying ``X-API-Key`` into logs.
_REDACTED_ENVIRONMENTS = frozenset({"staging", "production"})


def _redacts() -> bool:
    """Whether this process's records must be scrubbed before they are written."""
    return str(settings.PMRF_ENV).strip().lower() in _REDACTED_ENVIRONMENTS


class _RedactingLogFilter(logging.Filter):
    """Scrub one record in place, whatever sink is about to write it.

    Installed on the handlers of the root, ``uvicorn`` and ``uvicorn.access``
    loggers by :func:`setup_logging` when :func:`_redacts` is true. Named for
    what it does rather than for one environment: it runs on staging too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_message(record.msg)
        exc_info = record.exc_info
        if exc_info and exc_info[0] is not None:
            if isinstance(record.args, Mapping):
                record.args = {
                    key: redact_argument(value)
                    for key, value in record.args.items()
                }
            elif record.args is not None:
                record.args = tuple(redact_argument(arg) for arg in record.args)
            record.exc_info = None
            record.exc_text = None
        elif isinstance(record.args, Mapping):
            record.args = {
                key: redact_argument(value)
                for key, value in record.args.items()
            }
        elif record.args:
            record.args = tuple(redact_argument(arg) for arg in record.args)
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


def _install_filters_on(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        if any(isinstance(filter_, _RedactingLogFilter) for filter_ in handler.filters):
            continue
        handler.addFilter(_RedactingLogFilter())


def _install_filters() -> None:
    """Cover every handler a record can reach.

    ``uvicorn`` and ``uvicorn.access`` are named because uvicorn attaches its
    own handlers to those loggers; a record that stops there never reaches the
    root logger's handlers, so a filter installed only on the root would miss
    exactly the access lines that carry query strings.
    """
    for name in (None, "uvicorn", "uvicorn.access"):
        _install_filters_on(logging.getLogger(name))


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
    redact = _redacts()
    if redact:
        _install_filters()
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

    if redact:
        # Again on purpose: the two handlers above did not exist during the
        # first install, and a handler without the filter is a handler that
        # writes the URL.
        _install_filters()
