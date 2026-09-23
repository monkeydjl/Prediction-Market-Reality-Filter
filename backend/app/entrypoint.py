from __future__ import annotations

import importlib
import sys
from typing import Any


def load_application() -> Any:
    try:
        importlib.import_module("app.core.config")
    except RuntimeError as exc:
        print(
            f"Configuration refused startup: {type(exc).__name__}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    return importlib.import_module("app.main").app
