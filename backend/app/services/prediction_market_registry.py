"""Prediction-market platform registry.

Metadata-only catalogue used by UI/source-status surfaces. Planned sources are
listed here before they are added to active discovery so the product can show
where support is heading without pretending unverified adapters exist.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PredictionMarketPlatform:
    key: str
    name: str
    chain: str
    homepage_url: str
    search_url_template: str | None
    active_discovery: bool
    status_note: str


_PLATFORMS: tuple[PredictionMarketPlatform, ...] = (
    PredictionMarketPlatform(
        key="polymarket",
        name="Polymarket",
        chain="Polygon",
        homepage_url="https://polymarket.com/markets",
        search_url_template="https://polymarket.com/markets?_q={query}",
        active_discovery=True,
        status_note="Active discovery source.",
    ),
    PredictionMarketPlatform(
        key="kalshi",
        name="Kalshi",
        chain="Off-chain",
        homepage_url="https://kalshi.com/markets",
        search_url_template="https://kalshi.com/markets?search={query}",
        active_discovery=True,
        status_note="Active discovery source.",
    ),
    PredictionMarketPlatform(
        key="opinion",
        name="Opinion",
        chain="BNB Chain",
        homepage_url="https://app.opinion.trade/trending",
        search_url_template=None,
        active_discovery=False,
        status_note="API key source; adapter active when OPINION_API_KEY is configured.",
    ),
    PredictionMarketPlatform(
        key="limitless",
        name="Limitless",
        chain="Base",
        homepage_url="https://limitless.exchange/",
        search_url_template=None,
        active_discovery=True,
        status_note="Active public discovery source.",
    ),
    PredictionMarketPlatform(
        key="predict_fun",
        name="Predict.fun",
        chain="BNB Chain",
        homepage_url="https://predict.fun/",
        search_url_template=None,
        active_discovery=False,
        status_note="API key source; adapter active when PREDICT_FUN_API_KEY is configured.",
    ),
    PredictionMarketPlatform(
        key="probable",
        name="Probable",
        chain="BNB Chain",
        homepage_url="https://probable.finance/",
        search_url_template=None,
        active_discovery=False,
        status_note="Planned source; official adapter interface requires verification.",
    ),
)


def list_prediction_market_platforms() -> list[PredictionMarketPlatform]:
    return list(_PLATFORMS)


def active_discovery_platform_names() -> list[str]:
    return [platform.name for platform in _PLATFORMS if platform.active_discovery]
