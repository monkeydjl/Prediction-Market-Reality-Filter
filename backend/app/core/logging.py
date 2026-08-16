import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import settings


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
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
