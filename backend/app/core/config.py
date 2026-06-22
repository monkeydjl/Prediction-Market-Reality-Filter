from dotenv import load_dotenv
import os

load_dotenv()


def _env_bool(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str) -> list[str]:
    return [
        item.strip()
        for item in os.getenv(name, default).split(",")
        if item.strip()
    ]


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "deepseek-chat")
    LLM_STARTUP_CHECK_ENABLED: bool = _env_bool(
        "LLM_STARTUP_CHECK_ENABLED", "false"
    )
    LLM_STARTUP_CHECK_TIMEOUT_SECONDS: float = float(
        os.getenv("LLM_STARTUP_CHECK_TIMEOUT_SECONDS", "5.0")
    )

    CORS_ALLOWED_ORIGINS: list[str] = _env_csv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:8000,http://127.0.0.1:8000",
    )
    CORS_ALLOW_CREDENTIALS: bool = _env_bool("CORS_ALLOW_CREDENTIALS", "false")
    CORS_ALLOWED_METHODS: list[str] = _env_csv(
        "CORS_ALLOWED_METHODS",
        "GET,POST,PATCH,OPTIONS",
    )
    CORS_ALLOWED_HEADERS: list[str] = _env_csv(
        "CORS_ALLOWED_HEADERS",
        "Accept,Accept-Language,Content-Language,Content-Type,X-API-Key",
    )

    API_WRITE_KEY: str = os.getenv("API_WRITE_KEY", "")
    # Fail-closed by default: when API_WRITE_KEY is empty the app refuses to start
    # so a deploy that forgets to set it is never silently public. Set
    # ALLOW_OPEN_WRITES=true to explicitly opt into keyless (open) write endpoints
    # for local dev.
    ALLOW_OPEN_WRITES: bool = _env_bool("ALLOW_OPEN_WRITES", "false")
    RATE_LIMIT_ENABLED: bool = _env_bool("RATE_LIMIT_ENABLED", "true")
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120"))

    LOG_FILE: str = os.getenv(
        "LOG_FILE",
        os.path.join(os.path.dirname(__file__), "..", "..", "logs", "app.log"),
    )
    LOG_MAX_BYTES: int = int(os.getenv("LOG_MAX_BYTES", "10485760"))
    LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", "5"))

    # Base URL 可通过 .env 的 OPENAI_BASE_URL 覆盖
    # DeepSeek:  https://api.deepseek.com
    # DashScope: https://dashscope.aliyuncs.com/compatible-mode/v1
    # OpenAI:    留空（使用 SDK 默认值）
    DASHSCOPE_BASE_URL: str = os.getenv(
        "OPENAI_BASE_URL",
        "https://api.deepseek.com",
    )

    GNEWS_MAX_RESULTS: int = int(os.getenv("GNEWS_MAX_RESULTS", "10"))
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
    SPORTS_FACT_FILE: str = os.getenv(
        "SPORTS_FACT_FILE",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "sports_facts.json"
        ),
    )
    WORLD_CUP_DATA_FILE: str = os.getenv(
        "WORLD_CUP_DATA_FILE",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "world_cup_data.json"
        ),
    )
    WORLD_CUP_SOURCE_BUNDLE_FILE: str = os.getenv(
        "WORLD_CUP_SOURCE_BUNDLE_FILE",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "world_cup_source_bundle.json"
        ),
    )
    WORLD_CUP_SOURCE_BUNDLE_URL: str = os.getenv("WORLD_CUP_SOURCE_BUNDLE_URL", "")
    WORLD_CUP_MATCH_SOURCE_URL: str = os.getenv("WORLD_CUP_MATCH_SOURCE_URL", "")
    WORLD_CUP_MATCH_EVENTS_SOURCE_URL: str = os.getenv(
        "WORLD_CUP_MATCH_EVENTS_SOURCE_URL", ""
    )
    WORLD_CUP_LINEUPS_SOURCE_URL: str = os.getenv("WORLD_CUP_LINEUPS_SOURCE_URL", "")
    WORLD_CUP_STANDINGS_SOURCE_URL: str = os.getenv("WORLD_CUP_STANDINGS_SOURCE_URL", "")
    WORLD_CUP_PLAYER_AWARDS_SOURCE_URL: str = os.getenv(
        "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL", ""
    )
    WORLD_CUP_PLAYER_STATUS_SOURCE_URL: str = os.getenv(
        "WORLD_CUP_PLAYER_STATUS_SOURCE_URL", ""
    )
    WORLD_CUP_API_FOOTBALL_BASE_URL: str = os.getenv(
        "WORLD_CUP_API_FOOTBALL_BASE_URL",
        "https://v3.football.api-sports.io",
    )
    WORLD_CUP_API_FOOTBALL_API_KEY: str = os.getenv(
        "WORLD_CUP_API_FOOTBALL_API_KEY", ""
    )
    WORLD_CUP_API_FOOTBALL_LEAGUE_ID: str = os.getenv(
        "WORLD_CUP_API_FOOTBALL_LEAGUE_ID", "1"
    )
    WORLD_CUP_API_FOOTBALL_SEASON: str = os.getenv(
        "WORLD_CUP_API_FOOTBALL_SEASON", "2026"
    )
    WORLD_CUP_API_FOOTBALL_FETCH_EVENTS: bool = _env_bool(
        "WORLD_CUP_API_FOOTBALL_FETCH_EVENTS", "false"
    )
    WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS: bool = _env_bool(
        "WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS", "false"
    )
    WORLD_CUP_SPORTMONKS_API_TOKEN: str = os.getenv(
        "WORLD_CUP_SPORTMONKS_API_TOKEN", ""
    )
    WORLD_CUP_SPORTMONKS_FIXTURES_URL: str = os.getenv(
        "WORLD_CUP_SPORTMONKS_FIXTURES_URL", ""
    )
    WORLD_CUP_SPORTMONKS_STANDINGS_URL: str = os.getenv(
        "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""
    )
    WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL: str = os.getenv(
        "WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL", ""
    )
    WORLD_CUP_SOURCE_BUNDLE_TIMEOUT_SECONDS: float = float(
        os.getenv("WORLD_CUP_SOURCE_BUNDLE_TIMEOUT_SECONDS", "10.0")
    )
    WORLD_CUP_SOURCE_BUNDLE_MAX_BYTES: int = int(
        os.getenv("WORLD_CUP_SOURCE_BUNDLE_MAX_BYTES", "2097152")
    )
    WORLD_CUP_SOURCE_BUNDLE_USER_AGENT: str = os.getenv(
        "WORLD_CUP_SOURCE_BUNDLE_USER_AGENT",
        "Event Intelligence Platform research-contact@example.com",
    )
    WORLD_CUP_SOURCE_BUNDLE_AUTH_HEADER: str = os.getenv(
        "WORLD_CUP_SOURCE_BUNDLE_AUTH_HEADER", ""
    )
    WORLD_CUP_SOURCE_BUNDLE_AUTH_VALUE: str = os.getenv(
        "WORLD_CUP_SOURCE_BUNDLE_AUTH_VALUE", ""
    )
    WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED: bool = _env_bool(
        "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", "false"
    )
    WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE: str = os.getenv(
        "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "url"
    )
    WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE: bool = _env_bool(
        "WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE", "false"
    )
    WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC: int = int(
        os.getenv("WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC", "5")
    )
    WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC: int = int(
        os.getenv("WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC", "20")
    )
    WORLD_CUP_DATA_MAX_AGE_HOURS: float = float(
        os.getenv("WORLD_CUP_DATA_MAX_AGE_HOURS", "168")
    )
    # V2 loop store (SQLite). Holds the relational tables the feedback loop
    # depends on - starting with event_market_links (M0). Single file, no
    # server; sits alongside the JSON event_store rather than replacing it.
    LOOP_DB_FILE: str = os.getenv(
        "LOOP_DB_FILE",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "v2_loop.db"
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

    # Curated 2026 FIFA World Cup event source. Local/deterministic: contributes
    # high-interest sports questions to discovery without depending on a sports
    # data API. Set WORLD_CUP_SOURCE_ENABLED=false to disable it.
    WORLD_CUP_SOURCE_ENABLED: bool = _env_bool("WORLD_CUP_SOURCE_ENABLED", "true")
    WORLD_CUP_SOURCE_NAME: str = os.getenv(
        "WORLD_CUP_SOURCE_NAME",
        "2026 FIFA World Cup",
    )

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

    # Polymarket crypto-only candidate fetch. Opt-in: disabled unless
    # POLYMARKET_CRYPTO_FETCH_ENABLED is true. The default Polymarket fetch ranks
    # by volume, so geopolitics dominates the top-N and crypto markets never
    # reach the candidate pool. When enabled, discovery additionally runs a
    # crypto-only fetch (gamma-api tag filter + crypto-keyword gate) and merges
    # it into the candidate pool; dedupe keeps cross-source duplicates out. The
    # default-off keeps discovery behavior unchanged. The gamma-api tag parameter
    # is best-effort; a crypto-keyword gate backstops it so a wrong/empty tag
    # never floods the pool with non-crypto markets.
    POLYMARKET_CRYPTO_FETCH_ENABLED: bool = (
        os.getenv("POLYMARKET_CRYPTO_FETCH_ENABLED", "").strip().lower()
        in {"1", "true", "yes", "on"}
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

    # Event->market link auto-verification threshold (M0 identity gate). An
    # auto-resolve question match at or above this score is treated as a
    # verified link and is eligible to be scored; below it the link is recorded
    # but left unverified (pending human review) and is NOT scored - fail-closed,
    # so a fuzzy match never silently scores an event against the wrong outcome.
    # Default 1.0 = only exact normalized-question matches auto-verify.
    AUTO_VERIFY_THRESHOLD: float = float(
        os.getenv("AUTO_VERIFY_THRESHOLD", "1.0")
    )

    # Disagreement Diagnosis (M2). A committed prediction's raw edge (AI - market)
    # is trust-weighted into an adjusted edge: a divergence is only trusted in a
    # category where past resolved predictions actually beat the market.
    #   trust  = clamp(skill, 0, 1) once the category has CALIBRATION_FEEDBACK_MIN_SAMPLES
    #            scored predictions; below that the category is dormant and trust
    #            defaults to DIAGNOSIS_DORMANT_TRUST.
    #   adjusted_edge = raw_edge * trust * liquidity_factor, where liquidity_factor
    #            ramps 0->1 up to DIAGNOSIS_LIQUIDITY_FLOOR (unknown liquidity = 1.0).
    # The Decision Gate then sets act/watch/skip from the adjusted edge; "act"
    # requires a qualified (non-dormant) category, so an unproven segment caps at
    # "watch" no matter how large the divergence.
    DIAGNOSIS_DORMANT_TRUST: float = float(
        os.getenv("DIAGNOSIS_DORMANT_TRUST", "0.5")
    )
    DIAGNOSIS_LIQUIDITY_FLOOR: float = float(
        os.getenv("DIAGNOSIS_LIQUIDITY_FLOOR", "5000.0")
    )
    # Trust floor for a QUALIFIED segment. A category whose mean Brier is worse
    # than random (>0.25) scores negative skill -> clamp(skill,0,1) would be 0 ->
    # adjusted_edge 0 -> every new prediction skips -> skip rows are excluded from
    # segment_skill -> the segment's Brier can never improve: a self-reinforcing
    # absorbing state with no recovery. Flooring trust at a small positive value
    # keeps the penalty severe (still far below a trusted segment) while letting a
    # large enough raw edge occasionally clear the watch gate, so the category
    # keeps sampling and can climb back out. Does not apply to dormant segments
    # (those use DIAGNOSIS_DORMANT_TRUST).
    DIAGNOSIS_TRUST_FLOOR: float = float(
        os.getenv("DIAGNOSIS_TRUST_FLOOR", "0.1")
    )
    DECISION_ACT_EDGE: float = float(os.getenv("DECISION_ACT_EDGE", "10.0"))
    DECISION_WATCH_EDGE: float = float(os.getenv("DECISION_WATCH_EDGE", "3.0"))

    # Edge trajectory freshness (M3). An event's edge (AI - market) is tracked over
    # the audit snapshots. If the latest snapshot is older than EDGE_STALE_HOURS the
    # edge is "stale" (we have not re-evaluated recently); otherwise a material edge
    # (>= DECISION_WATCH_EDGE) that is holding near its peak is "fresh", one that has
    # shrunk from its peak is "decaying". The point is to act on edges while they are
    # live, not after the market has absorbed them.
    EDGE_STALE_HOURS: float = float(os.getenv("EDGE_STALE_HOURS", "72.0"))

    # Scheduled event discovery (M4 loop-unblock). The event layer freezes a
    # committed prediction per market-derived event on discovery; auto-resolve
    # (already scheduled at 22:30 UTC) scores them as markets settle. Without a
    # scheduled discovery run the loop never accrues predictions, so calibration
    # stays no_data and M2 trust stays dormant. Default-enabled because letting
    # the loop accrue data is the point; an operator without API keys (or who
    # does not want the per-run LLM cost) sets EVENT_DISCOVER_ENABLED false.
    EVENT_DISCOVER_ENABLED: bool = (
        os.getenv("EVENT_DISCOVER_ENABLED", "true").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    EVENT_DISCOVER_LIMIT: int = int(os.getenv("EVENT_DISCOVER_LIMIT", "10"))
    SCHEDULER_ENABLED: bool = _env_bool("SCHEDULER_ENABLED", "true")
    SCHEDULER_LOCK_ENABLED: bool = _env_bool("SCHEDULER_LOCK_ENABLED", "true")
    SCHEDULER_LOCK_FILE: str = os.getenv(
        "SCHEDULER_LOCK_FILE",
        LOOP_DB_FILE + ".scheduler.lock",
    )
    LLM_CONCURRENCY: int = int(os.getenv("LLM_CONCURRENCY", "4"))
    SCHEDULER_MISFIRE_GRACE_SECONDS: int = int(
        os.getenv("SCHEDULER_MISFIRE_GRACE_SECONDS", "86400")
    )
    SERVER_RELOAD: bool = _env_bool("SERVER_RELOAD", "false")


settings = Settings()
