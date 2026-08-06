"""Sentiment and news data aggregator for World Cup predictions.

Aggregates sentiment signals from:
1. Twitter/X - team mentions, player injuries, confidence
2. Reddit - r/soccer, r/worldcup discussion sentiment
3. RSS News Feeds - major sports news outlets
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

from app.utils.prediction_db import get_prediction_session, close_prediction_session
from app.utils.rss_fetch import parse_feed

logger = logging.getLogger(__name__)


# RSS News Feeds for World Cup coverage
NEWS_FEEDS = [
    {
        "name": "BBC Sport Football",
        "url": "https://feeds.bbci.co.uk/sport/football/rss.xml",
        "region": "UK"
    },
    {
        "name": "Goal Football",
        "url": "https://www.goal.com/en/feeds",
        "region": "US"
    },
    {
        "name": "The Guardian Football",
        "url": "https://www.theguardian.com/football/rss",
        "region": "UK"
    },
    {
        "name": "Sky Sports Football",
        "url": "https://www.skysports.com/rss/12040",
        "region": "UK"
    },
]


# Keywords for sentiment analysis (simple rule-based)
POSITIVE_KEYWORDS = [
    "win", "victory", "dominant", "strong", "confident", "momentum",
    "excellent", "impressive", "star", "form", "leading", "favorite"
]

NEGATIVE_KEYWORDS = [
    "loss", "defeat", "weak", "struggling", "injury", "concern",
    "doubt", "poor", "missing", "suspended", "problem", "crisis"
]


def calculate_simple_sentiment(text: str) -> float:
    """Calculate simple sentiment score from text.

    Args:
        text: Text to analyze

    Returns:
        Sentiment score between -1.0 (very negative) and 1.0 (very positive)
    """
    text_lower = text.lower()

    positive_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
    negative_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)

    total = positive_count + negative_count
    if total == 0:
        return 0.0

    # Normalize to -1 to 1
    score = (positive_count - negative_count) / total
    return max(-1.0, min(1.0, score))


async def _fetch_single_feed(
    feed: dict[str, str],
    team_name: str | None,
    per_feed_timeout: float,
) -> list[dict[str, Any]]:
    """Fetch and parse a single RSS feed, returning scored articles.

    Wrapped in ``asyncio.wait_for`` so one hung feed cannot stall the
    concurrent gather. Errors are logged and an empty list is returned so a
    single broken source never aborts the whole batch.
    """
    try:
        parsed = await asyncio.wait_for(
            asyncio.to_thread(parse_feed, feed["url"]),
            timeout=per_feed_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("RSS feed %s timed out after %.1fs", feed["name"], per_feed_timeout)
        return []
    except Exception as e:
        logger.error("Error fetching RSS feed %s: %s", feed["name"], e, exc_info=True)
        return []

    feed_articles: list[dict[str, Any]] = []
    for entry in parsed.entries[:20]:  # Limit per feed
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        link = entry.get("link", "")
        published = entry.get("published_parsed", None)

        # Skip if filtering by team and team not mentioned
        if team_name:
            if team_name.lower() not in title.lower() and team_name.lower() not in summary.lower():
                continue

        # Calculate sentiment
        text = f"{title} {summary}"
        sentiment = calculate_simple_sentiment(text)

        # Parse publish date
        pub_date = None
        if published:
            pub_date = datetime(*published[:6])

        article = {
            "title": title,
            "summary": summary[:300],  # Truncate
            "link": link,
            "source": feed["name"],
            "region": feed["region"],
            "published_at": pub_date.isoformat() if pub_date else None,
            "sentiment": sentiment,
            "mentions_team": team_name if team_name else None
        }

        feed_articles.append(article)
    return feed_articles


async def fetch_rss_news(
    team_name: str | None = None,
    max_articles: int = 50
) -> list[dict[str, Any]]:
    """Fetch news articles from RSS feeds.

    All feeds are fetched concurrently via ``asyncio.gather`` (previously
    sequential), with a per-feed timeout so a single hung source cannot stall
    the batch.

    Args:
        team_name: Filter articles mentioning this team (None = all articles)
        max_articles: Maximum number of articles to return

    Returns:
        List of article dicts with sentiment scores
    """
    per_feed_timeout = 15.0
    tasks = [
        _fetch_single_feed(feed, team_name, per_feed_timeout)
        for feed in NEWS_FEEDS
    ]
    # return_exceptions=True keeps one feed's failure from aborting the others;
    # _fetch_single_feed already swallows errors and returns [], so the items
    # here are always lists.
    results = await asyncio.gather(*tasks, return_exceptions=True)

    articles: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            # Defensive: _fetch_single_feed catches everything, but keep this
            # guard so an unexpected error never crashes the aggregator.
            logger.error("Unexpected error in RSS gather: %s", result, exc_info=True)
            continue
        articles.extend(result)

    # Sort by publish date (most recent first)
    articles.sort(key=lambda x: x["published_at"] or "", reverse=True)

    return articles[:max_articles]


async def fetch_reddit_sentiment(
    subreddit: str = "soccer",
    team_name: str | None = None,
    limit: int = 100
) -> dict[str, Any]:
    """Fetch Reddit posts and calculate sentiment (via RSS).

    Reddit provides RSS feeds for subreddits without API authentication.

    Args:
        subreddit: Subreddit name (e.g., "soccer", "worldcup")
        team_name: Filter posts mentioning this team
        limit: Maximum posts to analyze

    Returns:
        Sentiment summary dict
    """
    url = f"https://www.reddit.com/r/{subreddit}/hot/.rss?limit={limit}"

    try:
        # Parse Reddit RSS
        parsed = parse_feed(url)

        posts = []
        for entry in parsed.entries:
            title = entry.get("title", "")
            content = entry.get("content", [{}])[0].get("value", "")

            # Strip HTML from content
            if content:
                soup = BeautifulSoup(content, "html.parser")
                content = soup.get_text()

            # Filter by team if specified
            if team_name:
                if team_name.lower() not in title.lower() and team_name.lower() not in content.lower():
                    continue

            # Calculate sentiment
            text = f"{title} {content}"
            sentiment = calculate_simple_sentiment(text)

            posts.append({
                "title": title,
                "sentiment": sentiment,
                "link": entry.get("link", "")
            })

        # Aggregate sentiment
        if not posts:
            return {
                "subreddit": subreddit,
                "team_name": team_name,
                "posts_count": 0,
                "avg_sentiment": 0.0,
                "positive_ratio": 0.0
            }

        avg_sentiment = sum(p["sentiment"] for p in posts) / len(posts)
        positive_count = sum(1 for p in posts if p["sentiment"] > 0.1)
        positive_ratio = positive_count / len(posts)

        return {
            "subreddit": subreddit,
            "team_name": team_name,
            "posts_count": len(posts),
            "avg_sentiment": avg_sentiment,
            "positive_ratio": positive_ratio,
            "sample_posts": posts[:5]  # Top 5 for debugging
        }

    except Exception as e:
        logger.error("Error fetching Reddit r/%s: %s", subreddit, e, exc_info=True)
        return {
            "subreddit": subreddit,
            "team_name": team_name,
            "posts_count": 0,
            "avg_sentiment": 0.0,
            "positive_ratio": 0.0,
            "error": str(e)
        }


async def fetch_team_sentiment(team_name: str) -> dict[str, Any]:
    """Fetch aggregated sentiment for a team from multiple sources.

    Args:
        team_name: Team name to analyze

    Returns:
        Aggregated sentiment summary
    """
    # Fetch news articles
    news_articles = await fetch_rss_news(team_name=team_name, max_articles=20)

    # Calculate news sentiment
    news_sentiment = 0.0
    if news_articles:
        news_sentiment = sum(a["sentiment"] for a in news_articles) / len(news_articles)

    # Fetch Reddit sentiment
    reddit_sentiment = await fetch_reddit_sentiment(subreddit="soccer", team_name=team_name, limit=50)

    # Aggregate
    result = {
        "team_name": team_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "news": {
            "article_count": len(news_articles),
            "avg_sentiment": news_sentiment,
            "recent_headlines": [a["title"] for a in news_articles[:5]]
        },
        "reddit": {
            "posts_count": reddit_sentiment["posts_count"],
            "avg_sentiment": reddit_sentiment["avg_sentiment"],
            "positive_ratio": reddit_sentiment["positive_ratio"]
        },
        "overall_sentiment": (news_sentiment * 0.6 + reddit_sentiment["avg_sentiment"] * 0.4),
        "confidence": min(len(news_articles) / 20, reddit_sentiment["posts_count"] / 20)  # 0-1 based on data volume
    }

    return result


async def fetch_match_sentiment(home_team: str, away_team: str) -> dict[str, Any]:
    """Fetch sentiment for both teams in a match.

    Args:
        home_team: Home team name
        away_team: Away team name

    Returns:
        Match sentiment summary with comparison
    """
    home_sentiment = await fetch_team_sentiment(home_team)
    away_sentiment = await fetch_team_sentiment(away_team)

    # Calculate relative sentiment
    home_score = home_sentiment["overall_sentiment"]
    away_score = away_sentiment["overall_sentiment"]

    sentiment_diff = home_score - away_score
    confidence = (home_sentiment["confidence"] + away_sentiment["confidence"]) / 2

    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_sentiment": home_sentiment,
        "away_sentiment": away_sentiment,
        "sentiment_diff": sentiment_diff,  # Positive = home favored
        "confidence": confidence,
        "interpretation": interpret_sentiment_diff(sentiment_diff),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


def interpret_sentiment_diff(diff: float) -> str:
    """Interpret sentiment difference between teams.

    Args:
        diff: Sentiment difference (home - away)

    Returns:
        Human-readable interpretation
    """
    if abs(diff) < 0.1:
        return "neutral"
    elif diff > 0.3:
        return "strong_home_sentiment"
    elif diff > 0.1:
        return "moderate_home_sentiment"
    elif diff < -0.3:
        return "strong_away_sentiment"
    elif diff < -0.1:
        return "moderate_away_sentiment"
    else:
        return "neutral"


def cache_sentiment(data: dict[str, Any]) -> None:
    """Cache sentiment data to database.

    Args:
        data: Sentiment data from fetch_team_sentiment or fetch_match_sentiment
    """
    session = get_prediction_session()
    try:
        from app.models.world_cup_prediction import TeamSentiment

        team_name = data.get("team_name")
        if not team_name:
            return

        existing = session.query(TeamSentiment).filter_by(team_name=team_name).first()

        if existing:
            # Update existing
            existing.overall_sentiment = data["overall_sentiment"]
            existing.news_sentiment = data["news"]["avg_sentiment"]
            existing.reddit_sentiment = data["reddit"]["avg_sentiment"]
            existing.confidence = data["confidence"]
            existing.article_count = data["news"]["article_count"]
            existing.scraped_at = datetime.now(timezone.utc)
        else:
            # Insert new
            new_entry = TeamSentiment(
                team_name=team_name,
                overall_sentiment=data["overall_sentiment"],
                news_sentiment=data["news"]["avg_sentiment"],
                reddit_sentiment=data["reddit"]["avg_sentiment"],
                confidence=data["confidence"],
                article_count=data["news"]["article_count"],
                scraped_at=datetime.now(timezone.utc)
            )
            session.add(new_entry)

        session.commit()

    except Exception as e:
        session.rollback()
        logger.error("Error caching sentiment: %s", e, exc_info=True)

    finally:
        close_prediction_session(session)


def get_cached_sentiment(team_name: str, ttl_hours: int = 6) -> dict[str, Any] | None:
    """Get cached sentiment from database.

    Args:
        team_name: Team name
        ttl_hours: Cache TTL in hours (default: 6)

    Returns:
        Cached sentiment data, or None if not found or expired
    """
    session = get_prediction_session()
    try:
        from app.models.world_cup_prediction import TeamSentiment

        cached = session.query(TeamSentiment).filter_by(team_name=team_name).first()

        if not cached:
            return None

        # Check if expired
        # SQLite stores naive datetimes; attach UTC tzinfo before subtracting.
        scraped_at = cached.scraped_at.replace(tzinfo=timezone.utc) if cached.scraped_at else None
        if not scraped_at:
            return None
        age = datetime.now(timezone.utc) - scraped_at
        if age > timedelta(hours=ttl_hours):
            return None

        return {
            "team_name": cached.team_name,
            "overall_sentiment": cached.overall_sentiment,
            "news_sentiment": cached.news_sentiment,
            "reddit_sentiment": cached.reddit_sentiment,
            "confidence": cached.confidence,
            "article_count": cached.article_count,
            "cache_age_hours": age.total_seconds() / 3600
        }

    finally:
        close_prediction_session(session)
