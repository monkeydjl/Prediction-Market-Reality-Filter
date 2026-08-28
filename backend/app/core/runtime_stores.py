"""Which files are runtime state, and which are not.

Disaster recovery had two hand-maintained lists of the same thing. The backup
script named four settings; `restore_stores.py` repeated them with a comment
saying "Must stay in sync with scripts/backup_stores.py::_candidate_paths".
Nothing enforced either list, so every store added after the backup script was
written simply missed it — and the omissions were the largest bodies of
accumulated state on the install:

    kernel_predictions.db        19 tables, 33882 rows
    world_cup_predictions.db     13 tables,  2460 rows
    domain_reliability.db         learned per-domain priors
    sports_facts.json            curated match facts

`KERNEL_DB_FILE` could not have been listed by name until E10 turned that path
into a setting, which is exactly how a coverage list rots: it is complete on the
day it is written and nothing tells anyone when it stops being complete.

So the classification lives here, as data, with a written reason per row, and a
test asserts an **exact partition** of every path-shaped setting on
`Settings` — both directions. A new store cannot be added to the config without
either landing in a backup or being explicitly declared as something that does
not need one. `settings` is read lazily inside the functions rather than at
import, so a test (or `conftest`) that redirects a path is honoured.
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings

# Accumulated state. Losing one of these loses work that cannot be recomputed:
# committed predictions, settled outcomes, learned priors, curated facts.
STATE_STORES: dict[str, str] = {
    "EVENT_STORE_FILE": "the event records themselves, with frozen estimates",
    "EVENT_AUDIT_FILE": "append-only probability-snapshot audit trail",
    "EVENT_CACHE_FILE": (
        "per-question analysis cache; not data loss, but discarding it re-spends "
        "the LLM budget it was created to save"
    ),
    "LOOP_DB_FILE": "loop predictions, runs, market links, calibration history",
    "KERNEL_DB_FILE": (
        "sports kernel: committed predictions, prediction history, match "
        "outcomes and engine scores"
    ),
    "WORLD_CUP_PREDICTION_DB_FILE": (
        "World Cup fixtures, predictions, history and results; read by "
        "app/utils/prediction_db.py"
    ),
    "DOMAIN_RELIABILITY_DB_PATH": (
        "per-domain reliability learned from settled outcomes; feeds 40% of "
        "source overall_score, so losing it silently changes recommendations"
    ),
    "SPORTS_FACT_FILE": (
        "operator-imported sports facts; `sports_fact_service` offers import but "
        "no fetcher, so this file is the only copy of what was imported"
    ),
}

# Re-obtainable from the source that produced them. Excluded on purpose: a
# restore should re-fetch rather than resurrect a stale copy.
DERIVED_STORES: dict[str, str] = {
    "WORLD_CUP_DATA_FILE": "imported match data; re-importable from the source",
    "WORLD_CUP_SOURCE_BUNDLE_FILE": "fetched source bundle; re-fetchable",
    "LOL_DRY_RUN_FIXTURES_PATH": "dry-run fixture *input*, empty by default",
}

# Not state at all. Restoring one of these would be actively wrong.
EPHEMERAL_STORES: dict[str, str] = {
    "LOG_FILE": "log output; the app never reads it back",
    "SCHEDULER_LOCK_FILE": (
        "a lock file — restoring one would advertise a scheduler that is not "
        "running and suppress the next run"
    ),
}

# Archived alongside any SQLite store so a copy taken mid-transaction is not
# missing committed pages. Applied to every SQLite state store, not just the
# loop DB, which is how the loop DB alone came to have sidecar handling.
SQLITE_SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm")

_PATH_SETTING_SUFFIXES: tuple[str, ...] = ("_FILE", "_DIR", "_PATH")


def path_setting_names() -> set[str]:
    """Every path-shaped setting on `Settings`, found by scanning it.

    This is the population the partition test compares against, so it must be
    derived from the class rather than typed out.
    """
    cls = type(settings)
    return {
        name
        for name in dir(cls)
        if not name.startswith("_")
        and name.endswith(_PATH_SETTING_SUFFIXES)
        and isinstance(getattr(settings, name, None), str)
    }


def classified_setting_names() -> set[str]:
    """The union of the three declared categories."""
    return set(STATE_STORES) | set(DERIVED_STORES) | set(EPHEMERAL_STORES)


def state_setting_names() -> tuple[str, ...]:
    """Settings whose files belong in a backup, in a stable order."""
    return tuple(STATE_STORES)


def state_paths() -> list[Path]:
    """Configured path for each state store, whether or not it exists."""
    out: list[Path] = []
    for name in state_setting_names():
        value = getattr(settings, name, "")
        if value:
            out.append(Path(value))
    return out


def sidecar_paths(store: Path) -> list[Path]:
    """SQLite sidecars for `store`, or nothing for a non-SQLite store."""
    if store.suffix != ".db":
        return []
    return [Path(str(store) + suffix) for suffix in SQLITE_SIDECAR_SUFFIXES]


def backup_paths() -> list[Path]:
    """Resolved, existing files a backup should contain.

    Sidecars follow their store. Non-existent files are skipped: a store the
    operator has never populated is not an error.
    """
    out: list[Path] = []
    for store in state_paths():
        for candidate in [store, *sidecar_paths(store)]:
            if candidate.exists():
                out.append(candidate.resolve())
    return out
