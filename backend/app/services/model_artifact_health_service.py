"""Read-only health census of the three fitted model artifacts (E17).

Five production engines read a coefficient out of one of these three JSON files,
and **nothing reported the state of any of them**. Measured on 2026-08-30,
before this module existed:

* ``dixon_coles_params.json`` carries ``optimizer_success: false``. Its
  ``rho = -0.0763`` reaches ``DixonColesEngine`` and
  ``world_cup_rule_engine.calculate_outcome_probabilities`` (itself called by the
  GBM, AI and legacy prediction engines), and against ``rho=0`` it moves the
  served draw probability by **+0.0134 to +0.0207** across plausible xG pairs.
  So a coefficient steering every draw probability in the system came out of a
  fit that did not converge, and no log line, route, CLI or dashboard said so.
* Nine further keys the fitters write have zero readers in ``app/``:
  ``since_year``, ``min_team_matches``, ``team_count``, ``ref_date``,
  ``fitted_at``, ``feature_names``, ``dataset_stats``, ``validation_metrics``,
  ``lightgbm_version``.

The coefficients themselves are reported because they are committed model
parameters, not secrets. Per-team attack/defense vectors are deliberately not
returned: this is a health report, not a model dump.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: A fit is called stale past this age. One quarter: long enough that a
#: season's worth of new results has landed, short enough that an operator who
#: has never re-fit hears about it.
STALE_AFTER_DAYS = 90

#: No file at the declared path.
STATUS_MISSING = "missing"
#: File present, contents not parseable as the expected object.
STATUS_UNREADABLE = "unreadable"
#: The fitter reported that its optimizer did not converge.
STATUS_NOT_CONVERGED = "not_converged"
#: The fitter does not report convergence at all, so we cannot claim it did.
#: Distinct from ``ok`` on purpose -- "never measured" is not "measured clean",
#: the absence convention the optional discovery sources already use.
STATUS_UNKNOWN = "unknown"
#: Parseable, converged, and not stale.
STATUS_OK = "ok"

#: Worst-first, so a report's overall status is the worst of its members.
_SEVERITY: tuple[str, ...] = (
    STATUS_MISSING,
    STATUS_UNREADABLE,
    STATUS_NOT_CONVERGED,
    STATUS_UNKNOWN,
    STATUS_OK,
)

#: Coefficients each artifact actually serves, by artifact name. Declared as
#: data so the report cannot drift from what the engines read.
_SERVED_COEFFICIENTS: dict[str, tuple[str, ...]] = {
    "dixon_coles": ("rho", "home_advantage", "mu"),
    "btd": ("gamma", "home_advantage"),
    "gbm": (),
}

#: Keys describing what the fit was run over, reported for every artifact that
#: has them. These are the ones with zero readers today.
_SCOPE_KEYS: tuple[str, ...] = (
    "since_year",
    "min_team_matches",
    "half_life_days",
    "team_count",
)

#: Fit-quality numbers the fitters already compute and nothing reads. GBM records
#: no convergence flag at all, so its held-out RMSE is the only quality signal it
#: has -- reporting it is what keeps ``status="unknown"`` actionable instead of
#: merely honest.
_QUALITY_KEYS: tuple[str, ...] = (
    "home_rmse",
    "away_rmse",
    "empirical_draw_rate",
    "boosted_draw_prob_neutral_off",
    "final_neg_log_likelihood",
)


#: ``backend/data``, the directory every fitter writes into.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def artifact_slots() -> dict[str, Path]:
    """The three artifacts, named so a missing one is reported rather than absent.

    Each path is resolved exactly the way the engine that loads it resolves it:
    ``DIXON_COLES_PARAMS_FILE`` and ``BTD_PARAMS_FILE`` name a *file* and default
    off ``backend/data``, while ``GBM_DATA_DIR`` names a *directory*. Deriving the
    first two from ``GBM_DATA_DIR`` would report a file no engine reads.
    """
    return {
        "dixon_coles": Path(os.getenv(
            "DIXON_COLES_PARAMS_FILE",
            str(_DEFAULT_DATA_DIR / "dixon_coles_params.json"),
        )),
        "btd": Path(os.getenv(
            "BTD_PARAMS_FILE",
            str(_DEFAULT_DATA_DIR / "btd_params.json"),
        )),
        "gbm": Path(os.getenv(
            "GBM_DATA_DIR", str(_DEFAULT_DATA_DIR),
        )) / "gbm_features.json",
    }


def _age_days(value: Any, now: dt.date) -> int | None:
    """Whole days from an ISO date or datetime to ``now``; None if unusable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        if "T" in value:
            when = dt.datetime.fromisoformat(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            day = when.astimezone(dt.timezone.utc).date()
        else:
            day = dt.date.fromisoformat(value)
    except ValueError:
        return None
    return (now - day).days


def _sample_count(raw: dict[str, Any]) -> int | None:
    """The fit's sample count, wherever that fitter chose to put it."""
    direct = raw.get("sample_count")
    if isinstance(direct, bool):
        # bool is an int subclass; a flag is not a count.
        direct = None
    if isinstance(direct, (int, float)):
        return int(direct)
    stats = raw.get("dataset_stats")
    if isinstance(stats, dict):
        total = stats.get("total_samples")
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            return int(total)
    return None


#: Returned as the feature-identity verdict when the check itself could not run.
#: Distinct from ``"ok"``: "we could not look" is not "we looked and it is fine".
FEATURE_IDENTITY_UNAVAILABLE = "unavailable"


def _feature_identity(meta: dict[str, Any]) -> tuple[str, str | None]:
    """Run the GBM feature-identity check.

    Returns ``(verdict, problem)`` where ``verdict`` is ``"ok"``, the problem
    string, or :data:`FEATURE_IDENTITY_UNAVAILABLE`, and ``problem`` is the
    description only when a real disagreement was found.

    Imported lazily and defensively: ``app.services.world_cup_engines.__init__``
    pulls in the AI engine, which imports ``openai``. A health census must not be
    the thing that takes the route down when an optional dependency is absent --
    but it must not report a green verdict it never computed either, so the
    unavailable case gets its own value rather than collapsing into ``"ok"``.
    """
    try:
        from app.services.world_cup_engines.world_cup_gbm_features import (
            feature_identity_problem,
        )
    except Exception as exc:  # pragma: no cover - depends on install state
        logger.warning("GBM feature identity check unavailable: %s", exc)
        return FEATURE_IDENTITY_UNAVAILABLE, None
    problem = feature_identity_problem(meta)
    if problem is None:
        return "ok", None
    return problem, problem


def _worst_status(models: dict[str, dict[str, Any]]) -> str:
    """The worst status present, so one broken artifact cannot read as healthy."""
    if not models:
        return STATUS_MISSING
    present = {entry.get("status") for entry in models.values()}
    for candidate in _SEVERITY:
        if candidate in present:
            return candidate
    return STATUS_UNKNOWN


def inspect_artifact(name: str, path: Path, *, now: dt.date) -> dict[str, Any]:
    """Inspect one declared artifact slot. Never raises."""
    entry: dict[str, Any] = {
        "model": name,
        "path": path.name,
        "exists": False,
        "status": STATUS_MISSING,
        "fitted_at": None,
        "fitted_age_days": None,
        "ref_date": None,
        "ref_date_age_days": None,
        "sample_count": None,
        "optimizer_success": None,
        "stale": None,
        "coefficients": {},
        "training_scope": {},
        "fit_quality": {},
        "feature_identity": None,
        "detail": None,
    }

    if not path.exists():
        entry["detail"] = (
            f"{path.name} has not been fitted; the engines that read it fall back "
            "to their neutral defaults."
        )
        return entry

    entry["exists"] = True
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        entry["status"] = STATUS_UNREADABLE
        entry["detail"] = f"could not parse {path.name}: {exc}"
        return entry
    if not isinstance(raw, dict):
        entry["status"] = STATUS_UNREADABLE
        entry["detail"] = (
            f"{path.name} holds {type(raw).__name__}, expected a JSON object"
        )
        return entry

    entry["fitted_at"] = raw.get("fitted_at") if isinstance(
        raw.get("fitted_at"), str
    ) else None
    entry["fitted_age_days"] = _age_days(raw.get("fitted_at"), now)
    entry["ref_date"] = raw.get("ref_date") if isinstance(
        raw.get("ref_date"), str
    ) else None
    entry["ref_date_age_days"] = _age_days(raw.get("ref_date"), now)
    entry["sample_count"] = _sample_count(raw)

    age = entry["fitted_age_days"]
    entry["stale"] = None if age is None else age > STALE_AFTER_DAYS

    for key in _SERVED_COEFFICIENTS.get(name, ()):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            entry["coefficients"][key] = float(value)

    scope_source = raw.get("training_config")
    if not isinstance(scope_source, dict):
        scope_source = raw
    for key in _SCOPE_KEYS:
        value = scope_source.get(key)
        if value is None:
            value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            entry["training_scope"][key] = value

    # Each fitter parks these under its own nested key: BTD under `diagnostics`,
    # GBM under `validation_metrics`. Scanning both plus the top level means a
    # fitter that moves one does not silently drop it from the report.
    for source in (
        raw,
        raw.get("diagnostics"),
        raw.get("validation_metrics"),
    ):
        if not isinstance(source, dict):
            continue
        for key in _QUALITY_KEYS:
            value = source.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                entry["fit_quality"][key] = float(value)

    converged = raw.get("optimizer_success")
    if isinstance(converged, bool):
        entry["optimizer_success"] = converged
        if not converged:
            entry["status"] = STATUS_NOT_CONVERGED
            entry["detail"] = (
                f"{path.name} was written by a fit whose optimizer reported "
                "failure; the coefficients it serves are the last iterate, not a "
                "converged solution. Re-run the fitter."
            )
        else:
            entry["status"] = STATUS_OK
    else:
        # The GBM trainer records no convergence flag. Saying "ok" here would
        # claim a measurement nobody took.
        entry["status"] = STATUS_UNKNOWN
        entry["detail"] = (
            f"{path.name} records no optimizer_success flag, so convergence is "
            "unverified."
        )

    if name == "gbm":
        verdict, problem = _feature_identity(raw)
        entry["feature_identity"] = verdict
        if problem is not None:
            entry["status"] = STATUS_UNREADABLE
            entry["detail"] = (
                "the shipped boosters do not agree with the feature vector this "
                f"code builds ({problem}); the engine fails closed onto its Elo "
                "baseline. Re-run `python scripts/train_gbm_model.py`."
            )

    if entry["status"] == STATUS_OK and entry["stale"]:
        entry["detail"] = (
            f"converged, but fitted {age} days ago (> {STALE_AFTER_DAYS}); the "
            "coefficients predate recent results."
        )

    return entry


def collect_model_artifact_health(*, now: dt.date | None = None) -> dict[str, Any]:
    """Read-only health census over the declared artifact roster.

    Seeded from :func:`artifact_slots` rather than from a directory listing, so an
    artifact that was never fitted is reported with ``status="missing"`` instead of
    being silently absent from a report that then reads as healthy.
    """
    today = now or dt.datetime.now(dt.timezone.utc).date()
    models = {
        name: inspect_artifact(name, path, now=today)
        for name, path in artifact_slots().items()
    }

    problems = [
        {
            "model": entry["model"],
            "status": entry["status"],
            "detail": entry["detail"],
        }
        for entry in models.values()
        if entry["status"] != STATUS_OK or entry["stale"]
    ]

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stale_after_days": STALE_AFTER_DAYS,
        "total_models": len(models),
        "healthy_models": sum(
            1 for entry in models.values()
            if entry["status"] == STATUS_OK and not entry["stale"]
        ),
        "status": _worst_status(models),
        # Named explicitly so an operator does not have to diff a count against a
        # total to learn *which* artifact is unhealthy.
        "problems": problems,
        "models": models,
    }
