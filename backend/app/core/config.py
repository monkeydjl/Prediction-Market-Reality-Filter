from dotenv import load_dotenv
import os

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "deepseek-chat")

    # Base URL 可通过 .env 的 OPENAI_BASE_URL 覆盖
    # DeepSeek:  https://api.deepseek.com
    # DashScope: https://dashscope.aliyuncs.com/compatible-mode/v1
    # OpenAI:    留空（使用 SDK 默认值）
    DASHSCOPE_BASE_URL: str = os.getenv(
        "OPENAI_BASE_URL",
        "https://api.deepseek.com",
    )

    GNEWS_MAX_RESULTS: int = int(os.getenv("GNEWS_MAX_RESULTS", "10"))
    MARKET_SCAN_LIMIT: int = int(os.getenv("MARKET_SCAN_LIMIT", "5"))
    MEMORY_FILE: str = os.getenv(
        "MEMORY_FILE",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "agent_memory.json"
        ),
    )
    EVENT_STORE_FILE: str = os.getenv(
        "EVENT_STORE_FILE",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "event_store.json"
        ),
    )
    EVENT_AUDIT_FILE: str = os.getenv(
        "EVENT_AUDIT_FILE",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "event_audit.jsonl"
        ),
    )
    EVENT_CACHE_FILE: str = os.getenv(
        "EVENT_CACHE_FILE",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "event_cache.json"
        ),
    )
    OFFICIAL_RSS_URL: str = os.getenv(
        "OFFICIAL_RSS_URL",
        "https://www.federalreserve.gov/feeds/press_all.xml",
    )
    OFFICIAL_SOURCE_NAME: str = os.getenv("OFFICIAL_SOURCE_NAME", "Federal Reserve")
    SEC_EDGAR_RSS_URL: str = os.getenv(
        "SEC_EDGAR_RSS_URL",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom",
    )
    SEC_SOURCE_NAME: str = os.getenv("SEC_SOURCE_NAME", "SEC EDGAR")
    # SEC fair-access policy requires a declared User-Agent (name + contact).
    # Operators should override SEC_USER_AGENT with a real contact in .env.
    SEC_USER_AGENT: str = os.getenv(
        "SEC_USER_AGENT",
        "Event Intelligence Platform research-contact@example.com",
    )
    ECONOMIC_RSS_URL: str = os.getenv(
        "ECONOMIC_RSS_URL",
        "https://www.bls.gov/feed/bls_latest.rss",
    )
    ECONOMIC_SOURCE_NAME: str = os.getenv(
        "ECONOMIC_SOURCE_NAME", "U.S. Bureau of Labor Statistics"
    )
    # BLS (like SEC) returns 403 to requests without a declared User-Agent.
    ECONOMIC_USER_AGENT: str = os.getenv(
        "ECONOMIC_USER_AGENT",
        "Event Intelligence Platform research-contact@example.com",
    )

    # Manifold Markets: a second prediction-market event source (open public
    # API, no key). Set MANIFOLD_API_URL empty to disable the source.
    MANIFOLD_API_URL: str = os.getenv(
        "MANIFOLD_API_URL",
        "https://api.manifold.markets/v0/search-markets",
    )
    MANIFOLD_SOURCE_NAME: str = os.getenv("MANIFOLD_SOURCE_NAME", "Manifold")

    # Kalshi is a third prediction-market event source. The elections host serves
    # all categories and is reachable without an API key for read-only market
    # data. Set KALSHI_API_URL empty to disable the source.
    KALSHI_API_URL: str = os.getenv(
        "KALSHI_API_URL",
        "https://api.elections.kalshi.com/trade-api/v2/events",
    )
    KALSHI_SOURCE_NAME: str = os.getenv("KALSHI_SOURCE_NAME", "Kalshi")

    # Multi-model cross-validation: an independent second model re-estimates the
    # probability for the same question + evidence, surfaced as agreement /
    # divergence. Disabled unless CROSS_VALIDATION_MODEL is set. Base URL / key
    # fall back to the primary model's when left empty (so the common case is
    # cross-validating with a different model on the same provider).
    CROSS_VALIDATION_MODEL: str = os.getenv("CROSS_VALIDATION_MODEL", "")
    CROSS_VALIDATION_BASE_URL: str = os.getenv("CROSS_VALIDATION_BASE_URL", "")
    CROSS_VALIDATION_API_KEY: str = os.getenv("CROSS_VALIDATION_API_KEY", "")

    # Open-web structured event extraction: turn collected articles into native
    # candidate events (forward-looking questions), not just evidence. Disabled
    # unless OPEN_WEB_EXTRACTION_MODEL is set; runs on the primary provider/client.
    OPEN_WEB_EXTRACTION_MODEL: str = os.getenv("OPEN_WEB_EXTRACTION_MODEL", "")
    OPEN_WEB_SOURCE_NAME: str = os.getenv("OPEN_WEB_SOURCE_NAME", "Open Web")

    # Semantic news relevance via embeddings. Opt-in: disabled unless
    # EMBEDDING_MODEL is set. The default chat provider (DeepSeek) has NO
    # embeddings endpoint, so point EMBEDDING_BASE_URL / EMBEDDING_API_KEY at a
    # provider that does (DashScope, OpenAI, ...). When base_url is empty the
    # OpenAI default endpoint is used; when api_key is empty OPENAI_API_KEY is
    # reused. When disabled, news relevance uses the keyword signal only.
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "")
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "")
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "")

    # Calibration feedback loop. Opt-in: disabled unless
    # CALIBRATION_FEEDBACK_ENABLED is true. When enabled, the event layer uses
    # the Brier history of already-resolved events to (1) weight the market /
    # LLM / cross-validation probability signals by how accurate each has been
    # and (2) shrink a category's estimate toward its base rate when that
    # category has been historically overconfident. A breakdown is only used
    # once it has at least CALIBRATION_FEEDBACK_MIN_SAMPLES resolved samples;
    # below that the feedback is a no-op, so the published probability is
    # unchanged until enough outcomes have accumulated to be meaningful.
    CALIBRATION_FEEDBACK_ENABLED: bool = (
        os.getenv("CALIBRATION_FEEDBACK_ENABLED", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    CALIBRATION_FEEDBACK_MIN_SAMPLES: int = int(
        os.getenv("CALIBRATION_FEEDBACK_MIN_SAMPLES", "8")
    )

    # event_audit.jsonl retention. The audit log is append-only; once its line
    # count exceeds EVENT_AUDIT_COMPACTION_THRESHOLD, a compaction rewrites it
    # keeping at most EVENT_AUDIT_MAX_PER_EVENT snapshots per event (the most
    # recent ones), so the file stays bounded over long runs. Set the threshold
    # to 0 to disable compaction entirely.
    EVENT_AUDIT_COMPACTION_THRESHOLD: int = int(
        os.getenv("EVENT_AUDIT_COMPACTION_THRESHOLD", "5000")
    )
    EVENT_AUDIT_MAX_PER_EVENT: int = int(
        os.getenv("EVENT_AUDIT_MAX_PER_EVENT", "200")
    )


settings = Settings()
