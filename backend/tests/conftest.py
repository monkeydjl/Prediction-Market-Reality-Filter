"""Shared pytest fixtures for backend tests."""
import sys
from pathlib import Path

# Ensure backend/ is on sys.path (same pattern as individual test files).
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from unittest.mock import patch

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _disable_world_cup_data_staleness_gate():
    """Disable WORLD_CUP_DATA_MAX_AGE_HOURS staleness gate for all tests.

    Many test fixtures use hardcoded ``observed_at`` dates (e.g.
    2026-06-XX) that eventually exceed the default 168h staleness
    threshold as the wall clock advances, producing time-bomb failures.
    The staleness gate is a runtime concern; disabling it here keeps
    fixture-date tests wall-clock-independent.

    Tests that specifically assert staleness enforcement override this
    fixture via a tighter scope:
    - ``test_world_cup_source_bundle.py::test_configured_bundle_rejects_stale_source_metadata``
      passes ``max_age_hours=`` explicitly (overrides the setting).
    - ``test_world_cup_data_source_service.py`` patches
      ``WORLD_CUP_DATA_MAX_AGE_HOURS`` inside the test body (inner scope
      wins over this outer fixture).
    """
    with patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0):
        yield
