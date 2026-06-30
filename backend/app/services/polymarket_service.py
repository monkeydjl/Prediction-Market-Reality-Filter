import json
import logging
from typing import Any

import httpx

from app.models.market import MarketModel
from app.utils.market_utils import safe_float


POLYMARKET_API = "https://gamma-api.polymarket.com/markets"
logger = logging.getLogger(__name__)

ALLOWED_KEYWORDS = (
    # Politics & government
    "trump", "election", "president", "congress", "senate", "house",
    "governor", "democrat", "republican", "biden", "harris", "desantis",
    "impeach", "vote", "ballot", "primary", "nominee", "cabinet",
    "supreme court", "justice", "gop",
    # Geopolitics & conflict
    "war", "ukraine", "russia", "china", "taiwan", "iran", "israel",
    "nato", "sanctions", "treaty", "nuclear", "military", "invasion",
    "peace", "ceasefire", "nord korea", "north korea",
    # Economics & finance
    "fed", "inflation", "rate", "recession", "tariff", "gdp", "debt",
    "deficit", "unemployment", "jobs", "cpi", "ppi", "pmi",
    "nasdaq", "spy", "sp500", "s&p", "dow", "treasury", "yield",
    "stock", "market crash", "bubble",
    # Crypto & blockchain
    "bitcoin", "btc", "crypto", "ethereum", "eth", "solana", "sol",
    "xrp", "ripple", "dogecoin", "nft", "defi", "stablecoin",
    "binance", "coinbase", "sec", "etf", "token",
    # Technology & AI
    "ai", "openai", "anthropic", "google", "apple", "microsoft",
    "meta", "amazon", "nvidia", "tesla", "spacex", "robot",
    "gpt", "agi", "chip", "semiconductor", "quantum",
    # Energy & environment
    "oil", "gas", "energy", "climate", "hurricane", "earthquake",
    "tornado", "wildfire", "temperature", "carbon",
    # Health & science
    "covid", "pandemic", "fda", "vaccine", "drug", "pharma",
    "who", "disease", "cancer", "alzheimer",
    # Social & culture
    "abortion", "immigration", "gun", "lgbt", "marijuana", "cannabis",
    "strike", "union", "protest", "riot",
    # Sports (major events only)
    "world cup", "super bowl", "olympics", "nba", "nfl", "mlb",
    "champions league", "premier league",
    # Media & entertainment
    "oscar", "grammy", "emmy", "box office", "netflix", "disney",
)

# Crypto-only fetch gate (POLYMARKET_CRYPTO_FETCH_ENABLED). The gamma-api `tag_id`
# parameter is best-effort; this keyword set backstops it so a wrong/empty tag
# never floods the crypto pool with non-crypto markets. Substring match is fine
# here because these tokens (bitcoin/btc/crypto/ethereum/eth/solana/sol) are
# specific enough that accidental substring hits on a market *question* are
# rare, and the evidence-layer word-boundary match catches the rest downstream.
CRYPTO_KEYWORDS = (
    "bitcoin", "btc", "crypto", "ethereum", "eth", "solana", "sol",
)


async def fetch_markets(limit: int = 10, crypto_only: bool = False) -> list[MarketModel]:
    target = max(limit, 1)
    page_size = 100  # gamma-api caps at ~100 per request
    markets: list[MarketModel] = []
    offset = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while len(markets) < target:
            params = {
                "limit": str(page_size),
                "offset": str(offset),
                "closed": "false",
                "archived": "false",
                "order": "volume",
                "ascending": "false",
            }
            # Polymarket files crypto under a "crypto" tag. The tag_id filter is
            # best-effort: it narrows the ranked set toward crypto markets so they
            # are not crowded out by geopolitics in the volume ranking.
            if crypto_only:
                params["tag_id"] = "crypto"

            response = await client.get(POLYMARKET_API, params=params)
            response.raise_for_status()
            data = response.json()
            if not data:
                break  # no more pages

            page_count = 0
            for item in data:
                market = parse_market(item)
                if market is None:
                    continue
                if crypto_only:
                    if not is_crypto_market(market):
                        continue
                # Default path: no keyword gate. Polymarket orders by volume so
                # the top markets are already the most active/relevant.
                markets.append(market)
                page_count += 1
                if len(markets) >= target:
                    break

            if page_count == 0:
                break  # page had no parseable markets, stop paginating
            if len(data) < page_size:
                break  # last page (fewer items than requested)
            offset += page_size

    return markets


def parse_market(item: dict[str, Any]) -> MarketModel | None:
    try:
        question = str(item.get("question", "") or "").strip()
        if not question:
            return None

        # Use the parent event slug (events[0].slug), not the market slug, for
        # constructing https://polymarket.com/event/{event_slug} links.  The
        # market slug maps to a different URL path and produces 404s.
        events = item.get("events") or []
        event_slug = str((events[0] or {}).get("slug", "") or "") if events else ""

        yes_price, no_price = parse_outcome_prices(item.get("outcomePrices"))
        return MarketModel(
            id=str(item.get("id", "") or ""),
            slug=str(item.get("slug", "") or ""),
            event_slug=event_slug,
            question=question,
            yes_price=yes_price,
            no_price=no_price,
            volume=safe_float(item.get("volume"), 0.0),
            liquidity=safe_float(item.get("liquidity"), 0.0),
            closed=bool(item.get("closed", False)),
            archived=bool(item.get("archived", False)),
            resolved=bool(
                item.get("resolved", False)
                or item.get("isResolved", False)
                or item.get("resolutionStatus") == "resolved"
            ),
            end_date=str(item.get("endDate", "") or ""),
        )
    except Exception as exc:
        logger.warning("Failed to parse Polymarket market: %s", exc)
        return None


def parse_outcome_prices(raw_prices: Any) -> tuple[float, float]:
    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        if not isinstance(prices, list) or len(prices) < 2:
            raise ValueError("missing outcome prices")
        return safe_float(prices[0], 0.5), safe_float(prices[1], 0.5)
    except Exception:
        return 0.5, 0.5


def is_allowed_market(market: MarketModel) -> bool:
    if market.closed or market.archived or market.resolved:
        return False
    question = market.question.lower()
    return any(keyword in question for keyword in ALLOWED_KEYWORDS)


def is_crypto_market(market: MarketModel) -> bool:
    """True when a market's question names a crypto asset / the crypto category.

    Backstops the best-effort `tag_id=crypto` gamma-api filter: a wrong/empty
    tag must never flood the crypto pool with non-crypto markets. A resolved /
    closed market is rejected first (mirrors is_allowed_market).
    """
    if market.closed or market.archived or market.resolved:
        return False
    question = market.question.lower()
    return any(keyword in question for keyword in CRYPTO_KEYWORDS)
