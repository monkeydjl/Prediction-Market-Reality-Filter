"""Static betting / 竞猜 competition catalog (P1 product IA).

Mirrors the frontend ``lib/betting/competition-catalog`` so operators and the
API share one vocabulary. This is deliberately a pure data module — no DB,
no network — so ``GET /api/betting/catalog`` stays cheap and flag-free.

World Cup remains a separate track (``/api/world-cup/*``). Kernel competitions
point at ``/api/predictions/matches?competition=...``. Esports is catalogued
as ``coming_soon`` without fake markets.
"""
from __future__ import annotations

from typing import Any, Literal

CompetitionStatus = Literal["live", "kernel", "coming_soon"]
CompetitionTrack = Literal["kernel", "world_cup", "placeholder"]
CompetitionSection = Literal["football", "americas", "esports", "tools"]

SECTION_LABELS: dict[str, str] = {
    "football": "足球",
    "americas": "北美职业联赛",
    "esports": "电竞",
    "tools": "分析工具",
}

# Keep ids stable — FE deep links and tests depend on them.
BETTING_COMPETITIONS: list[dict[str, Any]] = [
    {
        "id": "world-cup",
        "sport": "football",
        "label": "世界杯",
        "short_label": "世界杯",
        "description": "赛程、分组出线、淘汰赛树与夺冠概率（专题 API，非 Kernel 流水线）",
        "status": "live",
        "href": "/sports/world-cup",
        "competition_code": "world_cup",
        "kernel_sport": None,
        "track": "world_cup",
        "section": "football",
    },
    {
        "id": "football",
        "sport": "football",
        "label": "足球联赛（Kernel）",
        "short_label": "足球",
        "description": "Kernel 足球赛程与多因子 / Elo-Odds 等引擎预测",
        "status": "kernel",
        "href": "/sports/betting/football",
        "competition_code": None,
        "kernel_sport": "football",
        "track": "kernel",
        "section": "football",
    },
    {
        "id": "epl",
        "sport": "football",
        "label": "英超",
        "short_label": "英超",
        "description": "英格兰足球超级联赛 — Kernel 足球路径",
        "status": "kernel",
        "href": "/sports/betting/epl",
        "competition_code": "epl",
        "kernel_sport": "football",
        "track": "kernel",
        "section": "football",
    },
    {
        "id": "laliga",
        "sport": "football",
        "label": "西甲",
        "short_label": "西甲",
        "description": "西班牙甲级联赛 — Kernel 足球路径",
        "status": "kernel",
        "href": "/sports/betting/laliga",
        "competition_code": "laliga",
        "kernel_sport": "football",
        "track": "kernel",
        "section": "football",
    },
    {
        "id": "bundesliga",
        "sport": "football",
        "label": "德甲",
        "short_label": "德甲",
        "description": "德国足球甲级联赛 — Kernel 足球路径",
        "status": "kernel",
        "href": "/sports/betting/bundesliga",
        "competition_code": "bundesliga",
        "kernel_sport": "football",
        "track": "kernel",
        "section": "football",
    },
    {
        "id": "serie-a",
        "sport": "football",
        "label": "意甲",
        "short_label": "意甲",
        "description": "意大利甲级联赛 — Kernel 足球路径",
        "status": "kernel",
        "href": "/sports/betting/serie-a",
        "competition_code": "serie_a",
        "kernel_sport": "football",
        "track": "kernel",
        "section": "football",
    },
    {
        "id": "ligue-1",
        "sport": "football",
        "label": "法甲",
        "short_label": "法甲",
        "description": "法国足球甲级联赛 — Kernel 足球路径",
        "status": "kernel",
        "href": "/sports/betting/ligue-1",
        "competition_code": "ligue_1",
        "kernel_sport": "football",
        "track": "kernel",
        "section": "football",
    },
    {
        "id": "nba",
        "sport": "basketball",
        "label": "NBA",
        "short_label": "NBA",
        "description": "北美职业篮球 — Kernel 篮球引擎与赛程",
        "status": "kernel",
        "href": "/sports/betting/nba",
        "competition_code": "nba",
        "kernel_sport": "basketball",
        "track": "kernel",
        "section": "americas",
    },
    {
        "id": "mlb",
        "sport": "baseball",
        "label": "MLB",
        "short_label": "MLB",
        "description": "美国职业棒球大联盟 — Kernel 棒球引擎",
        "status": "kernel",
        "href": "/sports/betting/mlb",
        "competition_code": "mlb",
        "kernel_sport": "baseball",
        "track": "kernel",
        "section": "americas",
    },
    {
        "id": "nhl",
        "sport": "hockey",
        "label": "NHL",
        "short_label": "NHL",
        "description": "国家冰球联盟 — Kernel 冰球引擎",
        "status": "kernel",
        "href": "/sports/betting/nhl",
        "competition_code": "nhl",
        "kernel_sport": "hockey",
        "track": "kernel",
        "section": "americas",
    },
    {
        "id": "esports",
        "sport": "esports",
        "label": "电竞",
        "short_label": "电竞",
        "description": "电竞赛事预测工作流规划中（暂无假数据 / 假盘口）",
        "status": "coming_soon",
        "href": "/sports/betting/esports",
        "competition_code": None,
        "kernel_sport": None,
        "track": "placeholder",
        "section": "esports",
    },
]

BETTING_TOOL_LINKS: list[dict[str, Any]] = [
    {
        "id": "edges",
        "href": "/sports/edges",
        "title": "体育 Edge",
        "description": "模型 vs 市场价格偏离，发现价值机会",
        "section": "tools",
    },
    {
        "id": "recommendations",
        "href": "/sports/recommendations",
        "title": "智能推荐",
        "description": "决策缺口与市场偏离驱动的推荐列表",
        "section": "tools",
    },
    {
        "id": "futures",
        "href": "/sports/futures",
        "title": "期货 / 冠军盘",
        "description": "赛季级冠军与期货市场概率",
        "section": "tools",
    },
    {
        "id": "markets",
        "href": "/sports/markets",
        "title": "市场桥接",
        "description": "预测市场链接、快照与 pending 核验",
        "section": "tools",
    },
    {
        "id": "kernel-all",
        "href": "/sports",
        "title": "全部 Kernel 赛程",
        "description": "跨运动比赛列表与引擎预测",
        "section": "tools",
    },
]


def build_catalog_payload() -> dict[str, Any]:
    """Public JSON body for GET /api/betting/catalog."""
    return {
        "version": 1,
        "sections": SECTION_LABELS,
        "competitions": list(BETTING_COMPETITIONS),
        "tools": list(BETTING_TOOL_LINKS),
        "notes": {
            "tracks": {
                "world_cup": "Dedicated /api/world-cup/* stack",
                "kernel": "Multi-sport Prediction Kernel /api/predictions/*",
                "placeholder": "IA only — no schedule or odds until data source lands",
            },
            "matches_filter": (
                "Kernel list: GET /api/predictions/matches"
                "?sport={kernel_sport}&competition={competition_code}"
            ),
        },
    }


def get_competition(competition_id: str) -> dict[str, Any] | None:
    for row in BETTING_COMPETITIONS:
        if row["id"] == competition_id:
            return dict(row)
    return None
