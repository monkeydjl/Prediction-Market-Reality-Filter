from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class LolSeriesRecord:
    external_id: str
    competition: str
    home_name: str
    away_name: str
    home_code: str
    away_code: str
    kickoff_utc: datetime
    best_of: int
    stage: str
    status: str


class LolScheduleSource(Protocol):
    def list_upcoming(self) -> list[LolSeriesRecord]: ...

    def get_result(self, external_id: str) -> dict | None: ...


class NullLolScheduleSource:
    def list_upcoming(self) -> list[LolSeriesRecord]:
        return []

    def get_result(self, external_id: str) -> dict | None:
        return None
