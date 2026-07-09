"""High-confidence event category inference from event text.

This is a display/filtering fallback for records whose source metadata only
contains generic prediction-market labels. Explicit source or base-rate
categories still win before these rules are consulted.
"""

from __future__ import annotations

import re
from typing import Any


_TEXT_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "monetary",
        (
            "central bank",
            "bank of england",
            "boe",
            "interest rate",
            "interest rates",
            "key rate",
            "official cash rate",
            "reserve bank",
            "rate cut",
            "rate hike",
            "\u592e\u884c",
            "\u5229\u7387",
            "\u964d\u606f",
            "\u52a0\u606f",
        ),
    ),
    (
        "sports_game",
        (
            "ufc",
            "mma",
            "knockout",
            "ko or tko",
            "tko",
            "main card",
            "heavyweight",
            "1+ shots",
            "shots",
            "\u7ec8\u7ed3",
            "\u62f3\u51fb",
            "\u683c\u6597",
        ),
    ),
    (
        "sports_general",
        (
            "lebron james",
            "cleveland cavaliers",
            "nba",
            "basketball",
            "\u52d2\u5e03\u6717",
            "\u8a79\u59c6\u65af",
            "\u9a91\u58eb\u961f",
            "\u7bee\u7403",
        ),
    ),
    (
        "crypto",
        (
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "crypto",
            "opensea",
            "fdv",
            "token",
            "hype up or down",
            "hype \u6da8\u8dcc",
            "\u6bd4\u7279\u5e01",
            "\u4ee5\u592a\u574a",
            "\u52a0\u5bc6",
        ),
    ),
    (
        "tech_product",
        (
            "gta vi",
            "grand theft auto",
            "trailer",
            "iphone",
            "apple event",
            "product launch",
            "software update",
            "app store",
            "robotaxi",
            "\u9884\u544a\u7247",
            "\u79d1\u6280\u4ea7\u54c1",
        ),
    ),
    (
        "geopolitics_general",
        (
            "visit russia",
            "russia",
            "ukraine",
            "nato",
            "un vote",
            "diplomatic",
            "treaty",
            "ceasefire",
            "war",
            "invade",
            "israel",
            "israeli",
            "litani river",
            "airspace",
            "\u4fc4\u7f57\u65af",
            "\u4e4c\u514b\u5170",
            "\u5317\u7ea6",
            "\u5916\u4ea4",
            "\u505c\u706b",
            "\u6218\u4e89",
            "\u4ee5\u8272\u5217",
            "\u5229\u5854\u5c3c\u6cb3",
            "\u9886\u7a7a",
        ),
    ),
    (
        "legal",
        (
            "epstein",
            "fbi",
            "raid",
            "raided",
            "storage units",
            "court",
            "lawsuit",
            "trial",
            "indictment",
            "subpoena",
            "\u7231\u6cfc\u65af\u5766",
            "\u641c\u67e5",
            "\u50a8\u7269\u67dc",
            "\u6cd5\u9662",
            "\u8bc9\u8bbc",
            "\u5ba1\u5224",
        ),
    ),
    (
        "politics_general",
        (
            "election",
            "candidate",
            "nomination",
            "senate",
            "governor",
            "president",
            "prime minister",
            "parliament",
            "referendum",
            "trump administration",
            "population decrease",
            "population decline",
            "\u9009\u4e3e",
            "\u5019\u9009\u4eba",
            "\u63d0\u540d",
            "\u53c2\u8bae\u9662",
            "\u603b\u7edf",
            "\u5dde\u957f",
            "\u4eba\u53e3\u51cf\u5c11",
            "\u4eba\u53e3\u4e0b\u964d",
        ),
    ),
    (
        "weather_event",
        (
            "weather",
            "hurricane",
            "storm",
            "rainfall",
            "temperature",
            "\u5929\u6c14",
            "\u98d3\u98ce",
            "\u964d\u96e8",
            "\u6c14\u6e29",
        ),
    ),
    (
        "health_event",
        (
            "vaccine",
            "fda approval",
            "clinical trial",
            "pandemic",
            "disease",
            "\u75ab\u82d7",
            "\u4e34\u5e8a\u8bd5\u9a8c",
            "\u75be\u75c5",
        ),
    ),
    (
        "company_earnings",
        (
            "earnings",
            "revenue",
            "profit",
            "quarterly results",
            "\u8d22\u62a5",
            "\u8425\u6536",
            "\u5229\u6da6",
        ),
    ),
    (
        "ipo",
        (
            "ipo",
            "initial public offering",
            "\u4e0a\u5e02",
        ),
    ),
)


def _matches(needle: str, text: str) -> bool:
    if needle.isascii() and any(ch.isalnum() for ch in needle):
        pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return needle in text


def infer_category_from_text(*values: Any) -> str | None:
    text = " ".join(str(value or "") for value in values).lower()
    if not text.strip():
        return None
    for category, needles in _TEXT_CATEGORY_RULES:
        if any(_matches(needle, text) for needle in needles):
            return category
    return None
