from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.kernel.kernel_db import KernelMatchFixture, get_kernel_session

logger = logging.getLogger(__name__)


def _parse_kickoff(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _season_for(kickoff: datetime) -> str:
    return "dry-run"


def import_lol_series_file(path: str | Path) -> int:
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)

    series = payload.get("series") or []
    if not series:
        return 0

    session = get_kernel_session()
    upserted = 0
    try:
        now = datetime.now(timezone.utc)
        for item in series:
            external_id = item["external_id"]
            match_id = f"lol-{external_id}"
            kickoff = _parse_kickoff(item["kickoff_utc"])
            best_of = int(item.get("best_of") or 1)
            venue = f"Bo{best_of}"
            season = _season_for(kickoff)

            existing = session.get(KernelMatchFixture, match_id)
            if existing:
                existing.home_team = item["home_name"]
                existing.away_team = item["away_name"]
                existing.kickoff_utc = kickoff
                existing.stage = item.get("stage")
                existing.status = item.get("status", "scheduled")
                existing.venue = venue
                existing.competition = item["competition"]
                existing.season = season
                existing.updated_at = now
            else:
                session.add(
                    KernelMatchFixture(
                        match_id=match_id,
                        competition=item["competition"],
                        season=season,
                        home_team=item["home_name"],
                        away_team=item["away_name"],
                        kickoff_utc=kickoff,
                        stage=item.get("stage"),
                        status=item.get("status", "scheduled"),
                        venue=venue,
                        created_at=now,
                        updated_at=now,
                    )
                )
            upserted += 1
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("Failed to import LoL series from %s: %s", path, exc)
        raise
    finally:
        session.close()
    return upserted
