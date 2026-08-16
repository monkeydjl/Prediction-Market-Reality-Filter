"""
rss_service.py — 改进版
========================
新增：
- CoinDesk（加密专项）
- The Block（加密专项）
- Reuters Politics（政治专项）
- Reuters Business（宏观专项）
- 每个 feed 独立并发，最慢单源决定总耗时（约 4-6s）
- 去掉已确认返回空数据的 Reuters TopNews / AP News
"""

import asyncio
import logging
from functools import partial

from app.utils.failure_policy import fail_closed_empty_list, log_service_failure
from app.utils.rss_fetch import parse_feed

logger = logging.getLogger(__name__)

# (名称, URL, 类别标签)
RSS_FEEDS = [
    # 政治/选举
    ("Politico",         "https://rss.politico.com/politics-news.xml",        "politics"),
    ("The Hill",         "https://thehill.com/feed/",                          "politics"),
    ("AP News",          "https://rsshub.app/apnews/topics/politics",         "politics"),
    # 金融/宏观
    ("WSJ Markets",      "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",     "finance"),
    ("Financial Times",  "https://www.ft.com/?format=rss",                    "finance"),
    ("CNBC Economy",     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258", "finance"),
    # 加密货币（Polymarket 最大类别）
    ("CoinTelegraph",    "https://cointelegraph.com/rss",                      "crypto"),
    ("Decrypt",          "https://decrypt.co/feed",                            "crypto"),
    ("CoinDesk",         "https://www.coindesk.com/arc/outboundfeeds/rss/",   "crypto"),
    # AI/科技
    ("TechCrunch",       "https://techcrunch.com/feed/",                       "tech"),
    # Sports / World Cup context
    ("BBC Football",     "https://feeds.bbci.co.uk/sport/football/rss.xml",    "sports"),
    ("Guardian Football", "https://www.theguardian.com/football/rss",          "sports"),
]


def _fetch_one(name: str, url: str, limit: int) -> list[dict]:
    """同步抓取单个 RSS 源。在线程池中执行。"""
    try:
        feed = parse_feed(url)
        return [
            {
                "title":     entry.get("title", ""),
                "summary":   entry.get("summary", ""),
                "source":    name,
                "link":      entry.get("link", ""),
                "published": entry.get("published", ""),
            }
            for entry in feed.entries[:limit]
        ]
    except Exception as exc:
        return fail_closed_empty_list(
            logger,
            "rss_feed",
            exc,
            context={"name": name, "url": url},
        )


async def fetch_news(limit: int = 5) -> list:
    """
    并发抓取所有 RSS 源（每个 feed 独立线程）。
    总耗时 ≈ 最慢单源（约 4-6 秒），而非各源之和（约 40 秒）。
    """
    from app.models.news import NewsModel

    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, partial(_fetch_one, name, url, limit))
        for name, url, _ in RSS_FEEDS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    articles = []
    for (name, _, _), result in zip(RSS_FEEDS, results):
        # BaseException, not Exception: a cancelled executor task's
        # CancelledError comes back as a result value, and it is not an
        # Exception. Here the `isinstance(result, list)` guard below already
        # keeps it out of `articles`, so the narrow guard only cost the warning
        # log — but it is the same shape that is live elsewhere, so it matches.
        if isinstance(result, BaseException):
            log_service_failure(
                logger,
                "rss_task",
                result,
                policy="fail_closed_empty_list",
                context={"name": name},
            )
            continue
        if isinstance(result, list):
            for item in result:
                articles.append(NewsModel(
                    title=item["title"],
                    summary=item["summary"],
                    source=item["source"],
                    link=item["link"],
                    published=item["published"],
                ))
    return articles
