"""prediction_store.py
==================
Durable store for committed, point-in-time predictions (SQLite-backed).

A prediction freezes, at analysis time, the AI probability vs the market price
for an event plus the raw edge between them, and the market snapshot
(price / liquidity / volume / contract) folded inline. One event, one prediction
(commitment, not trajectory): the verdict is frozen at first sight via
INSERT ... ON CONFLICT(event_id) DO NOTHING, so a re-scan never overwrites it -
the committed estimate is exactly what we believed at decision time. Probability
and edge trajectories live in the audit log, not here. At resolve time the frozen
prediction is scored against the settled outcome (Brier, act->scored /
watch+skip->observed) - the loop's honest, never-recomputed calibration signal;
a non-genuine resolution (identity conflict / void) closes it as `voided` instead
(no score, off the opportunity surface).

Backed by the same SQLite loop file as event_market_link_store
(sqlite_db.loop_db_path()). See docs/user/V2_ROADMAP.md Milestone 1 and
docs/user/DATABASE_DESIGN.md.
"""

import threading
import uuid
from typing import Any

from app.core.config import settings
from app.memory.event_market_link_store import upsert_link
from app.models.event import Prediction
from app.services.calibration_service_event import brier_score, grade, skill_score
from app.services.diagnosis_service import diagnose
from app.utils.helpers import utc_now
from app.utils import sqlite_db
from app.utils.sqlite_db import reading, writing

# Schema as discrete statements. _SCHEMA (joined) is used for the idempotent
# first-run create via executescript; the individual statements in
# _SCHEMA_STATEMENTS are replayed one-by-one with conn.execute() inside _migrate
# so the table rebuild rides a single transaction (executescript would force an
# implicit COMMIT mid-rebuild, breaking atomicity — see _migrate).
_CREATE_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS predictions (
    id                  TEXT PRIMARY KEY,
    event_id            TEXT NOT NULL UNIQUE,
    contract_id         TEXT NOT NULL DEFAULT '',
    platform            TEXT NOT NULL DEFAULT '',
    base_rate_category  TEXT NOT NULL DEFAULT 'unknown',
    ai_probability      REAL NOT NULL,
    market_probability  REAL NOT NULL,
    raw_edge            REAL NOT NULL,
    trust               REAL,
    adjusted_edge       REAL,
    liquidity           REAL NOT NULL DEFAULT 0.0,
    volume              REAL NOT NULL DEFAULT 0.0,
    decision            TEXT NOT NULL DEFAULT 'tracked',
    liquidity_factor    REAL,
    qualified           INTEGER,
    segment_n           INTEGER,
    segment_min_samples INTEGER,
    segment_skill       REAL,
    created_at          TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'open',
    actual_outcome      REAL,
    brier_score         REAL,
    resolved_at         TEXT
)
"""
_CREATE_STATUS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_pred_status ON predictions(status)"
)
_SCHEMA_STATEMENTS = (_CREATE_PREDICTIONS, _CREATE_STATUS_INDEX)
_SCHEMA = ";\n".join(stmt.strip() for stmt in _SCHEMA_STATEMENTS) + ";"

# Columns added after the M1 schema; _migrate adds any missing on an existing DB.
_MIGRATIONS = {
    "base_rate_category": "TEXT NOT NULL DEFAULT 'unknown'",
    "trust": "REAL",
    "adjusted_edge": "REAL",
    "liquidity_factor": "REAL",
    "qualified": "INTEGER",
    "segment_n": "INTEGER",
    "segment_min_samples": "INTEGER",
    "segment_skill": "REAL",
}

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()
_SCHEMA_VERSION = 3


def _migrate(conn: Any) -> None:
    """Bring an existing predictions table up to the current shape. Idempotent:
    a fresh table already matches, so every step is a no-op there.

    1. Add any missing columns (cheap ALTER ADD COLUMN).
    2. Restore the one-event-one-prediction model: if the table lacks the
       UNIQUE(event_id) constraint (it was dropped during the short-lived M3
       multi-row experiment), collapse each event to a single row and rebuild the
       table WITH UNIQUE(event_id). Collapse keeps the open row if present, else
       the most recently resolved row; superseded/extra rows are discarded.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(predictions)")}
    for column, decl in _MIGRATIONS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE predictions ADD COLUMN {column} {decl}")

    # Does the table already enforce UNIQUE(event_id)? (auto-index origin 'u')
    has_unique_event = False
    for idx in conn.execute("PRAGMA index_list(predictions)").fetchall():
        if idx["unique"] and idx["origin"] == "u":
            icols = [r["name"] for r in conn.execute(f"PRAGMA index_info('{idx['name']}')")]
            if icols == ["event_id"]:
                has_unique_event = True
                break
    if has_unique_event:
        return

    # No UNIQUE(event_id): this DB went through the multi-row experiment. Collapse
    # to one row per event before rebuilding with the constraint (a straight
    # rebuild would fail on duplicate event_ids). Keep the open row per event if
    # one exists (the standing commitment), else the latest by created_at; drop
    # the rest (superseded history is not part of the commitment model).
    #
    # All steps run via conn.execute() (NOT executescript) so the whole rebuild
    # rides the single transaction opened by writing(): executescript() issues an
    # implicit COMMIT before running, which would commit the RENAME + CREATE before
    # the data copy — a failure mid-copy would then leave an empty predictions
    # table with the rows stranded in predictions_old and no way to roll back.
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(predictions)")]
    not_null_defaults = {
        "contract_id": "''", "platform": "''", "base_rate_category": "'unknown'",
        "liquidity": "0.0", "volume": "0.0", "decision": "'tracked'",
        "created_at": "''", "status": "'open'",
    }
    col_csv = ", ".join(cols)
    select_csv = ", ".join(
        f"COALESCE({c}, {not_null_defaults[c]}) AS {c}" if c in not_null_defaults else c
        for c in cols
    )
    conn.execute("ALTER TABLE predictions RENAME TO predictions_old")
    # Index names are global; after the RENAME they stay attached to
    # predictions_old but keep their names, so a plain CREATE INDEX IF NOT EXISTS
    # below would no-op and leave the rebuilt table unindexed. Drop them first so
    # the rebuild recreates them on the new table.
    conn.execute("DROP INDEX IF EXISTS idx_pred_status")
    conn.execute("DROP INDEX IF EXISTS idx_pred_category")
    for stmt in _SCHEMA_STATEMENTS:
        conn.execute(stmt)
    # Keep one row per event without ROW_NUMBER() (unavailable on SQLite < 3.25):
    # a correlated subquery selects, for each event, the rowid of its preferred
    # survivor — open first (status='open' sorts last so DESC puts it first), then
    # most recent created_at, then highest rowid as a stable tiebreak.
    conn.execute(
        f"""
        INSERT INTO predictions ({col_csv})
        SELECT {select_csv} FROM predictions_old p
        WHERE p.rowid = (
            SELECT q.rowid FROM predictions_old q
            WHERE q.event_id = p.event_id
            ORDER BY (q.status='open') DESC, q.created_at DESC, q.rowid DESC
            LIMIT 1
        )
        """
    )
    conn.execute("DROP TABLE predictions_old")


def _ensure_schema(path: str) -> None:
    """Create the table on first use of a given DB path, then migrate (idempotent)."""
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
        with writing(path) as conn:
            conn.executescript(_SCHEMA)
            _migrate(conn)
            # Index the segment column only after _migrate guarantees it exists
            # (an existing M1 table gains it via ALTER above).
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pred_category "
                "ON predictions(base_rate_category)"
            )
            sqlite_db.record_schema_version(conn, "predictions", _SCHEMA_VERSION)
        _INITIALIZED.add(path)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def freeze_prediction(record: dict[str, Any]) -> dict[str, Any] | None:
    """Freeze one committed point-in-time prediction from an analyzed event record.

    Fail-closed and market-gated: only market-derived events get a prediction
    (source.type == "prediction_market" with a contract id and a real market
    price). News / open-web events carry a placeholder baseline and no contract,
    so they are skipped (returns None) - no market, no edge.

    One event, one prediction (commitment, not trajectory): the verdict is frozen
    at first sight and never overwritten. A re-scan is a no-op (INSERT ... ON
    CONFLICT(event_id) DO NOTHING returns the existing committed row unchanged).
    Probability/edge trajectories live in the audit log, not here.
    """
    source = record.get("source") or {}
    if source.get("type") != "prediction_market":
        return None
    contract_id = str(source.get("source_id") or "")
    if not contract_id:
        return None

    probability = record.get("probability") or {}
    ai = probability.get("estimated")
    market = probability.get("baseline")
    if ai is None or market is None:
        return None
    ai = _num(ai)
    market = _num(market)

    event_id = record.get("event_id")
    if not event_id:
        return None

    # Disagreement Diagnosis (M2): trust-weight the raw edge by how well past
    # resolved predictions scored in THIS category, and set act/watch/skip. The
    # verdict is computed at first freeze (point-in-time) and frozen with the row.
    category = str((record.get("legacy_analysis") or {}).get("base_rate_category") or "unknown")
    raw_edge = round(ai - market, 2)
    liquidity = _num(source.get("liquidity"))
    diag = diagnose(raw_edge, segment_skill(category), liquidity)

    prediction = Prediction(
        event_id=event_id,
        contract_id=contract_id,
        platform=str(source.get("platform") or ""),
        base_rate_category=category,
        ai_probability=ai,
        market_probability=market,
        raw_edge=raw_edge,
        trust=diag["trust"],
        adjusted_edge=diag["adjusted_edge"],
        liquidity=liquidity,
        volume=_num(source.get("volume")),
        decision=diag["decision"],
        liquidity_factor=diag["liquidity_factor"],
        qualified=diag["qualified"],
        segment_n=diag["segment_n"],
        segment_min_samples=diag["segment_min_samples"],
        segment_skill=diag["segment_skill"],
        created_at=utc_now(),
    )

    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with writing(path) as conn:
        # DO NOTHING on conflict: the first commitment is frozen forever.
        cursor = conn.execute(
            """
            INSERT INTO predictions (
                id, event_id, contract_id, platform, base_rate_category,
                ai_probability, market_probability, raw_edge, trust, adjusted_edge,
                liquidity, volume, decision, liquidity_factor, qualified,
                segment_n, segment_min_samples, segment_skill, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                str(uuid.uuid4()), prediction.event_id, prediction.contract_id,
                prediction.platform, prediction.base_rate_category,
                prediction.ai_probability, prediction.market_probability,
                prediction.raw_edge, prediction.trust, prediction.adjusted_edge,
                prediction.liquidity, prediction.volume, prediction.decision,
                prediction.liquidity_factor,
                int(prediction.qualified) if prediction.qualified is not None else None,
                prediction.segment_n, prediction.segment_min_samples,
                prediction.segment_skill,
                prediction.created_at,
            ),
        )
        inserted = cursor.rowcount > 0

    # Seed a verified event->contract link at FIRST freeze only. The contract_id
    # comes from source.source_id - the market this event was DERIVED from, i.e.
    # ground-truth identity, not a fuzzy text match. This lets auto_resolve settle
    # the event by contract id on the first pass (the contract-first PRIMARY
    # path), instead of depending on an exact question-text match at settlement.
    # Without it, get_verified_link is None on first resolve and wording drift
    # between freeze and settlement silently blocks resolution.
    #
    # Gated on `inserted` so a re-scan (ON CONFLICT DO NOTHING) does not rewrite
    # linked_at or silently re-verify a link a human deliberately un-verified.
    if inserted:
        upsert_link(
            event_id,
            market_name=str(source.get("platform") or ""),
            contract_id=contract_id,
            market_question=str(record.get("event_title") or ""),
            link_method="freeze",
            link_confidence=1.0,
            verified=True,
        )
    return get_prediction(event_id)


def score_prediction(event_id: str, actual_outcome: float) -> dict[str, Any] | None:
    """Resolve an event's open frozen prediction against the settled outcome.

    Only an `act` row becomes a `scored` calibration sample - the V2 invariant
    "Only act rows are scored" (V2_ROADMAP step 7). A `watch` / `skip` row is
    still closed out and gets its Brier recorded, but moves to terminal status
    `observed`: its outcome is kept for diagnostics yet excluded from the
    prediction calibration / segment skill / realized edge, so the loop learns
    from the opportunities it actually committed to act on, not from everything
    it ever froze.

    Computes the Brier of the frozen ai_probability vs actual_outcome (both
    0-100), records actual_outcome / brier_score / resolved_at, and moves the
    row open -> scored (act) or open -> observed (watch/skip). No-op (returns
    None) when the event has no open prediction (never committed, or already
    resolved). Idempotent on re-resolve.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            "SELECT * FROM predictions WHERE event_id=? AND status='open'",
            (event_id,),
        ).fetchone()
    if row is None:
        return None

    # Only act rows are scored; watch/skip are observed (recorded, not calibrated).
    new_status = "scored" if row["decision"] == "act" else "observed"
    brier = round(brier_score(row["ai_probability"], actual_outcome), 4)
    with writing(path) as conn:
        conn.execute(
            """
            UPDATE predictions
            SET status=?, actual_outcome=?, brier_score=?, resolved_at=?
            WHERE event_id=? AND status='open'
            """,
            (new_status, round(_num(actual_outcome), 2), brier, utc_now(), event_id),
        )
    return get_prediction(event_id)


def void_prediction(event_id: str) -> dict[str, Any] | None:
    """Close an event's open prediction without scoring it - the terminal state
    for a non-genuine resolution (identity conflict -> invalid, or a voided
    market). Moves open -> `voided`: no Brier, no calibration, and crucially it
    leaves the opportunity surface (list_open_opportunities reads status='open'),
    so an invalidated event stops showing up as actionable. No-op (None) when the
    event has no open prediction. Idempotent."""
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            "SELECT id FROM predictions WHERE event_id=? AND status='open'",
            (event_id,),
        ).fetchone()
    if row is None:
        return None
    with writing(path) as conn:
        conn.execute(
            "UPDATE predictions SET status='voided', resolved_at=? "
            "WHERE event_id=? AND status='open'",
            (utc_now(), event_id),
        )
    return get_prediction(event_id)


def get_prediction(event_id: str) -> dict[str, Any] | None:
    """The committed prediction for an event (one per event), or None when the
    event was never frozen."""
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            "SELECT * FROM predictions WHERE event_id=?",
            (event_id,),
        ).fetchone()
    return dict(row) if row else None


def list_recent(limit: int = 50) -> list[dict[str, Any]]:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        rows = conn.execute(
            "SELECT * FROM predictions ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_open_opportunities(
    decisions: tuple[str, ...] = ("act", "watch"),
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Open (unresolved) committed predictions worth a human's attention, ranked
    by absolute adjusted edge. `decisions` filters by the Decision Gate verdict
    (default act + watch; "skip" is excluded). This is the opportunity surface."""
    if not decisions:
        return []
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    placeholders = ",".join("?" for _ in decisions)
    with reading(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM predictions
            WHERE status='open' AND decision IN ({placeholders})
            ORDER BY ABS(COALESCE(adjusted_edge, 0)) DESC
            LIMIT ?
            """,
            (*decisions, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def segment_skill(category: str) -> dict[str, Any]:
    """Conditional calibration for one category - the trust signal the
    Disagreement Diagnosis reads to weight a divergence.

    Returns {n, mean_brier, skill} over the category's resolved act+watch
    predictions (status in scored/observed, decision in act/watch). skip rows are
    excluded: a skip means we essentially agreed with the market, an easy
    forecast whose low Brier would inflate trust. act AND watch are counted on
    purpose - this is the qualification gate (n >= min_samples leaves dormancy),
    and an act-only gate could never bootstrap a fresh category (no act history
    -> never qualified -> never acts). The headline calibration_summary stays
    act-only; this trust gate is deliberately broader so the loop can learn.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, AVG(brier_score) AS mean_brier
            FROM predictions
            WHERE status IN ('scored', 'observed') AND decision IN ('act', 'watch')
              AND base_rate_category=?
            """,
            (category,),
        ).fetchone()
    n = row["n"] or 0
    if n == 0:
        return {"n": 0, "mean_brier": None, "skill": None}
    mean_brier = round(row["mean_brier"], 4)
    return {"n": n, "mean_brier": mean_brier, "skill": round(skill_score(mean_brier), 4)}


def calibration_summary() -> dict[str, Any]:
    """Calibration over scored predictions: an overall block (mean Brier + grade,
    count, mean raw edge) plus a per-category breakdown. Empty (no_data) until
    committed predictions have resolved. The by_category block is what M2 uses to
    know which segments are trustworthy.

    Act-only: every aggregate filters decision='act' (status='scored' is already
    act-only via score_prediction, but the filter is explicit so the calibration
    signal can never include a watch/skip row). watch/skip resolve to 'observed'
    and stay out of this summary - the loop reports on what it committed to act
    on, not on everything it froze.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, AVG(brier_score) AS mean_brier,
                   AVG(raw_edge) AS mean_edge
            FROM predictions WHERE status='scored' AND decision='act'
            """,
        ).fetchone()
        cat_rows = conn.execute(
            """
            SELECT base_rate_category AS cat, COUNT(*) AS n,
                   AVG(brier_score) AS mean_brier
            FROM predictions WHERE status='scored' AND decision='act'
            GROUP BY base_rate_category
            """,
        ).fetchall()
        segment_rows = conn.execute(
            """
            SELECT base_rate_category AS cat, COUNT(*) AS n,
                   AVG(brier_score) AS mean_brier
            FROM predictions
            WHERE status IN ('scored', 'observed') AND decision IN ('act', 'watch')
            GROUP BY base_rate_category
            """,
        ).fetchall()
        scored_rows = conn.execute(
            """
            SELECT raw_edge, market_probability, actual_outcome
            FROM predictions WHERE status='scored' AND decision='act'
            """,
        ).fetchall()
    by_category = {
        r["cat"]: {
            "n": r["n"],
            "brier_score": round(r["mean_brier"], 4),
            "skill_score": round(skill_score(r["mean_brier"]), 4),
            "grade": grade(r["mean_brier"]),
        }
        for r in cat_rows
    }
    min_samples = settings.CALIBRATION_FEEDBACK_MIN_SAMPLES
    segments = {
        r["cat"]: {
            "n": r["n"],
            "brier_score": round(r["mean_brier"], 4),
            "skill_score": round(skill_score(r["mean_brier"]), 4),
            "grade": grade(r["mean_brier"]),
            "segment_min_samples": min_samples,
            "qualified": (r["n"] or 0) >= min_samples,
        }
        for r in segment_rows
    }
    # Realized vs predicted edge: did reality move the way we said the market was
    # wrong? realized = sign(raw_edge) * (actual_outcome - market_probability);
    # positive means our divergence beat the market. directional_hit_rate is the
    # fraction of scored predictions with positive realized edge.
    realized_vals = [
        ((r["raw_edge"] > 0) - (r["raw_edge"] < 0))
        * (r["actual_outcome"] - r["market_probability"])
        for r in scored_rows
    ]
    n = row["n"] or 0
    if n == 0:
        return {"n": 0, "brier_score": None, "grade": "no_data",
                "mean_raw_edge": None, "realized_edge": None,
                "directional_hit_rate": None, "by_category": by_category,
                "segment_min_samples": min_samples, "segments": segments}
    mean_brier = round(row["mean_brier"], 4)
    return {
        "n": n,
        "brier_score": mean_brier,
        "grade": grade(mean_brier),
        "mean_raw_edge": round(row["mean_edge"], 2),
        "realized_edge": round(sum(realized_vals) / len(realized_vals), 2),
        "directional_hit_rate": round(
            sum(1 for v in realized_vals if v > 0) / len(realized_vals), 4
        ),
        "by_category": by_category,
        "segment_min_samples": min_samples,
        "segments": segments,
    }
