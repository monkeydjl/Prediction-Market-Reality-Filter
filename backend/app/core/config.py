from dotenv import load_dotenv
import os


def _resolve_env_file() -> str:
    """Return the env file path for the current PMRF_ENV.

    ``development`` (default) -> ``.env``
    ``staging`` -> ``.env.staging``
    ``production`` -> ``.env.production``

    The environment-specific file overrides the base ``.env`` (loaded first
    without override, then the env file with override=True).
    """
    pmrf_env = os.getenv("PMRF_ENV", "development").strip().lower()
    if pmrf_env == "staging":
        return ".env.staging"
    if pmrf_env == "production":
        return ".env.production"
    return ".env"


def _load_env_files() -> None:
    """Load base .env then environment-specific file (override=True)."""
    load_dotenv()  # base .env, no override
    env_file = _resolve_env_file()
    if env_file != ".env":
        load_dotenv(env_file, override=True)


_load_env_files()


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
        "Accept,Accept-Language,Content-Language,Content-Type,"
        "X-API-Key,X-Client-Source,X-Operator",
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
    # Whether the deployment sits behind a trusted reverse proxy (nginx/caddy/
    # Cloudflare) that overwrites X-Forwarded-For / X-Real-IP with the real
    # client address. When false (default, direct-to-internet / dev) those
    # headers are ignored because anyone can spoof them, so rate limiting keys
    # off request.client.host only. When true the proxy headers are honored.
    TRUSTED_PROXY_HEADER: bool = _env_bool("TRUSTED_PROXY_HEADER", "false")

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

    # News sentiment & full-text enrichment (LLM-powered news analysis for
    # prediction-market events). All default ON so behavior is unchanged
    # unless an operator explicitly disables one. The sentiment flag gates
    # the per-event LLM call; the full-text flag gates the trafilatura HTTP
    # fetch (which the sentiment prompt then consumes when available).
    NEWS_SENTIMENT_ENABLED: bool = _env_bool(
        "NEWS_SENTIMENT_ENABLED", "true"
    )
    NEWS_SENTIMENT_MAX_ARTICLES: int = int(
        os.getenv("NEWS_SENTIMENT_MAX_ARTICLES", "6")
    )
    NEWS_FULL_TEXT_FETCH_ENABLED: bool = _env_bool(
        "NEWS_FULL_TEXT_FETCH_ENABLED", "true"
    )
    NEWS_FULL_TEXT_MAX_ARTICLES: int = int(
        os.getenv("NEWS_FULL_TEXT_MAX_ARTICLES", "5")
    )
    # Per-article evidence breakdown (Stage: evidence decomposition). Emits
    # structured direction/strength/credibility/rationale per article from the
    # existing news sentiment LLM call. Explanation/audit layer only; does NOT
    # participate in evidence_profile or ai_probability.
    EVIDENCE_BREAKDOWN_ENABLED: bool = _env_bool(
        "EVIDENCE_BREAKDOWN_ENABLED", "true"
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
    WORLD_CUP_STATISTICS_SOURCE_URL: str = os.getenv("WORLD_CUP_STATISTICS_SOURCE_URL", "")
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
    WORLD_CUP_API_FOOTBALL_FETCH_STATISTICS: bool = _env_bool(
        "WORLD_CUP_API_FOOTBALL_FETCH_STATISTICS", "false"
    )
    WORLD_CUP_API_FOOTBALL_MAX_DETAIL_CALLS: int = int(
        os.getenv("WORLD_CUP_API_FOOTBALL_MAX_DETAIL_CALLS", "100")
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
    WORLD_CUP_SPORTMONKS_LINEUPS_URL: str = os.getenv(
        "WORLD_CUP_SPORTMONKS_LINEUPS_URL", ""
    )
    WORLD_CUP_SPORTMONKS_CARDS_URL: str = os.getenv(
        "WORLD_CUP_SPORTMONKS_CARDS_URL", ""
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
    WORLD_CUP_MATCHDAY_REFRESH_ENABLED: bool = _env_bool(
        "WORLD_CUP_MATCHDAY_REFRESH_ENABLED", "false"
    )
    WORLD_CUP_MATCHDAY_REFRESH_INTERVAL_MINUTES: int = int(
        os.getenv("WORLD_CUP_MATCHDAY_REFRESH_INTERVAL_MINUTES", "30")
    )
    WORLD_CUP_MATCHDAY_REFRESH_WINDOW_HOURS: int = int(
        os.getenv("WORLD_CUP_MATCHDAY_REFRESH_WINDOW_HOURS", "6")
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
    # AES passphrase for backup archives (scripts/backup_stores.py). When set,
    # backups are written as pyzipper AES-256 encrypted zips; when empty the
    # legacy plaintext zip is produced. Must be set in any environment where
    # the backup volume is not otherwise protected at rest.
    BACKUP_ENCRYPTION_KEY: str = os.getenv("BACKUP_ENCRYPTION_KEY", "")
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

    # Metaculus is a fourth prediction-question event source (community forecasts
    # on long-horizon science/tech/AI/policy questions). Unlike the market
    # sources, it requires an API token — register at metaculus.com, copy the
    # token from Account Settings, and set METACULUS_API_TOKEN. The source is
    # auto-disabled when the token is empty, so an unset source never makes
    # authenticated network calls.
    METACULUS_API_URL: str = os.getenv(
        "METACULUS_API_URL",
        "https://www.metaculus.com/api2/posts/",
    )
    METACULUS_API_TOKEN: str = os.getenv("METACULUS_API_TOKEN", "")
    METACULUS_SOURCE_NAME: str = os.getenv("METACULUS_SOURCE_NAME", "Metaculus")

    # Curated 2026 FIFA World Cup event source. Local/deterministic: contributes
    # high-interest sports questions to discovery without depending on a sports
    # data API. Set WORLD_CUP_SOURCE_ENABLED=false to disable it.
    WORLD_CUP_SOURCE_ENABLED: bool = _env_bool("WORLD_CUP_SOURCE_ENABLED", "false")
    WORLD_CUP_SOURCE_NAME: str = os.getenv(
        "WORLD_CUP_SOURCE_NAME",
        "2026 FIFA World Cup",
    )

    # World Cup dynamic score prediction database (SQLite)
    WORLD_CUP_PREDICTION_DB_FILE: str = os.getenv(
        "WORLD_CUP_PREDICTION_DB_FILE",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "world_cup_predictions.db"
        ),
    )

    # The Odds API - Betting odds data source
    # Free tier: 500 requests/month
    # Register at: https://the-odds-api.com/
    ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
    ODDS_API_ENABLED: bool = _env_bool("ODDS_API_ENABLED", "false")

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
    OPEN_WEB_ENABLED: bool = _env_bool("OPEN_WEB_ENABLED", "false")
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
        os.getenv("CALIBRATION_FEEDBACK_ENABLED", "true").strip().lower()
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
    # Default 0.90 = high-quality fuzzy matches auto-verify. The FUZZY_THRESHOLD
    # (0.82) already gates minimum match quality; the verify gate only needs to
    # reject the lowest-quality fuzzy matches, not demand exact identity.
    AUTO_VERIFY_THRESHOLD: float = float(
        os.getenv("AUTO_VERIFY_THRESHOLD", "0.90")
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
    DECISION_ACT_EDGE: float = float(os.getenv("DECISION_ACT_EDGE", "6.0"))
    DECISION_WATCH_EDGE: float = float(os.getenv("DECISION_WATCH_EDGE", "2.0"))
    # Paper-trade generation: when true, events with decision in {act,
    # provisional_act, watch} automatically create a simulated trade row.
    # When false (legacy), no paper trades are created. Set
    # PAPER_TRADE_WATCH_ENABLED=true to also include watch-grade events.
    PAPER_TRADE_ENABLED: bool = _env_bool("PAPER_TRADE_ENABLED", "true")
    PAPER_TRADE_WATCH_ENABLED: bool = _env_bool("PAPER_TRADE_WATCH_ENABLED", "true")

    # Actionable conclusions (Stage 3): surface the already-computed LONG/SHORT
    # legacy signal as a structured actionable_recommendation on event records.
    # When true, build_event_record adds an actionable_recommendation dict with
    # direction (YES/NO/AVOID/WAIT) + confidence + suggested allocation. When
    # false, the field is always None (legacy behavior).
    ACTIONABLE_RECOMMENDATION_ENABLED: bool = _env_bool(
        "ACTIONABLE_RECOMMENDATION_ENABLED", "true"
    )
    # Auto-translate event titles to Simplified Chinese during discovery.
    # When true (default), every event gets a Chinese title even if the LLM
    # analysis skips title_zh — a separate lightweight translation call runs.
    AUTO_TRANSLATE_TITLES: bool = _env_bool("AUTO_TRANSLATE_TITLES", "true")
    # Cold-start bypass: when a category is dormant (0 resolved samples) but
    # the adjusted edge exceeds act_edge, emit "provisional_act" instead of
    # "watch". This unblocks the system during cold-start. Disable to restore
    # old behavior (dormant categories never earn "act" regardless of edge).
    COLD_START_BYPASS_ENABLED: bool = _env_bool(
        "COLD_START_BYPASS_ENABLED", "true"
    )

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
    EVENT_DISCOVER_LIMIT: int = int(os.getenv("EVENT_DISCOVER_LIMIT", "100"))
    # Per-source weight multipliers for discovery.  Each source is asked for
    # ``limit * weight`` candidates instead of the flat ``limit``.  Polymarket
    # is the primary prediction-market source (highest weight), Kalshi is
    # secondary, Manifold is supplementary.  Open Web (LLM extraction from
    # news) is kept below market sources so the event mix is dominated by
    # real market prices, not news-derived speculation.
    SOURCE_WEIGHTS: dict[str, float] = {
        "Polymarket": float(os.getenv("SOURCE_WEIGHT_POLYMARKET", "3.0")),
        "Kalshi": float(os.getenv("SOURCE_WEIGHT_KALSHI", "1.0")),
        "Manifold": float(os.getenv("SOURCE_WEIGHT_MANIFOLD", "0.3")),
        "Open Web": float(os.getenv("SOURCE_WEIGHT_OPEN_WEB", "0.5")),
        "Polymarket Crypto": float(os.getenv("SOURCE_WEIGHT_POLYMARKET_CRYPTO", "1.0")),
        "World Cup": 0.3,
        "Metaculus": 0.5,
    }
    SCHEDULER_ENABLED: bool = _env_bool("SCHEDULER_ENABLED", "true")
    SCHEDULER_LOCK_ENABLED: bool = _env_bool("SCHEDULER_LOCK_ENABLED", "true")
    SCHEDULER_LOCK_FILE: str = os.getenv(
        "SCHEDULER_LOCK_FILE",
        LOOP_DB_FILE + ".scheduler.lock",
    )
    LLM_CONCURRENCY: int = int(os.getenv("LLM_CONCURRENCY", "4"))
    # Hard timeout for a single discover_events scan (seconds). On timeout,
    # already-completed candidates are saved as partial results; still-running
    # tasks are cancelled. Default 10 minutes — generous enough for a full
    # limit=100 scan at 4 concurrency, strict enough to unblock the scheduler.
    EVENT_DISCOVER_TIMEOUT_SECONDS: int = int(
        os.getenv("EVENT_DISCOVER_TIMEOUT_SECONDS", "600")
    )
    # Phase 1 — Decision Explanation + Conflict Layer (default OFF).
    # When enabled, build_decision_quality() runs inside analyze_event and
    # attaches a decision_quality overlay block to the record. The block is
    # a pure audit layer — it MUST NOT mutate actionable_recommendation or
    # ai_probability. See docs/superpowers/specs/2026-06-30-decision-quality-engine-design.md
    DECISION_QUALITY_ENABLED: bool = _env_bool("DECISION_QUALITY_ENABLED", "false")
    DECISION_QUALITY_MAX_EVIDENCE_ITEMS: int = int(
        os.getenv("DECISION_QUALITY_MAX_EVIDENCE_ITEMS", "3")
    )
    DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD: float = float(
        os.getenv("DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD", "0.40")
    )
    DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD: float = float(
        os.getenv("DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD", "0.20")
    )
    # Phase 2 — Market Quality Layer (default OFF). When enabled,
    # market_quality_service scores market feasibility (spread, liquidity,
    # volume) for prediction_market sources and may downgrade strong
    # recommendations to WAIT. Only fires for source.type == "prediction_market"
    # (Polymarket, Kalshi); Metaculus (prediction_question) and manual sources
    # are excluded. See docs/superpowers/audits/market-quality-field-audit.md
    # for field availability per adapter.
    MARKET_QUALITY_ENABLED: bool = _env_bool("MARKET_QUALITY_ENABLED", "false")
    MARKET_MAX_SPREAD_PCT: float = float(
        os.getenv("MARKET_MAX_SPREAD_PCT", "12")
    )
    MARKET_MIN_LIQUIDITY: float = float(
        os.getenv("MARKET_MIN_LIQUIDITY", "1000")
    )
    MARKET_MIN_VOLUME: float = float(
        os.getenv("MARKET_MIN_VOLUME", "1000")
    )
    MARKET_STALE_AFTER_MINUTES: int = int(
        os.getenv("MARKET_STALE_AFTER_MINUTES", "180")
    )
    MARKET_QUALITY_SCORE_THRESHOLD: float = float(
        os.getenv("MARKET_QUALITY_SCORE_THRESHOLD", "0.5")
    )
    # ── Execution Quality (Plan 3 §3.5) ────────────────────────────────
    # Defaults to OFF — byte-identical to pre-Plan-3 when off (no
    # execution_quality key attached, no guardrail rule 4 firing).
    EXECUTION_QUALITY_ENABLED: bool = os.getenv(
        "EXECUTION_QUALITY_ENABLED", "false"
    ).lower() in ("1", "true", "yes")
    # Max acceptable bid-ask spread as percentage of mid price (0-100).
    # Reuses MARKET_MAX_SPREAD_PCT by default but can be overridden.
    EXECUTION_MAX_SPREAD_PCT: float = float(
        os.getenv("EXECUTION_MAX_SPREAD_PCT", "12")
    )
    # Price considered stale if last_updated is older than this (seconds).
    EXECUTION_STALE_PRICE_SECONDS: int = int(
        os.getenv("EXECUTION_STALE_PRICE_SECONDS", "300")
    )
    # Minimum liquidity for execution feasibility.
    EXECUTION_MIN_LIQUIDITY: float = float(
        os.getenv("EXECUTION_MIN_LIQUIDITY", "1000")
    )
    # Target order size (shares) for slippage estimation. RESERVED: not yet
    # consumed by execution_quality_service — slippage currently uses the
    # half-spread proxy. Kept as a config knob for a future depth-aware
    # slippage model that needs order-book data (currently no source exposes
    # order book depth). Adjusting this value has NO effect on output today.
    EXECUTION_TARGET_ORDER_SIZE: float = float(
        os.getenv("EXECUTION_TARGET_ORDER_SIZE", "100")
    )
    # Platform fee rate as percentage of notional (0-100).
    EXECUTION_FEE_RATE_PCT: float = float(
        os.getenv("EXECUTION_FEE_RATE_PCT", "1.0")
    )
    # Guardrail rule 4: when True, executable=False forces YES/NO → WAIT.
    GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT: bool = os.getenv(
        "GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT", "true"
    ).lower() in ("1", "true", "yes")
    # Phase 3 — Prediction Outcome Calibration (default OFF). When enabled,
    # freeze_prediction captures a richer snapshot (question, recommendation,
    # confidence, evidence_strength, conflict_score, market_quality_score,
    # source_platform) and score_prediction computes direction_correct,
    # edge_bucket, and confidence_bucket. calibration_bucket_summary()
    # aggregates resolved predictions by edge_bucket × confidence_bucket.
    # When disabled, the snapshot columns stay NULL and buckets are not
    # computed — byte-identical to pre-Phase-3. This is a long-term feature:
    # it only becomes valuable after enough resolved samples exist.
    PREDICTION_CALIBRATION_ENABLED: bool = _env_bool(
        "PREDICTION_CALIBRATION_ENABLED", "false"
    )
    # Phase 4 — Source Reliability overlay (default OFF). When enabled,
    # analyze_event attaches a source_reliability overlay block for events
    # that have a non-empty evidence_breakdown (prediction_market,
    # prediction_question, open_web). Scores the diversity and quality of
    # news sources (tier-weighted, domain diversity, trusted ratio) and
    # downgrades YES/NO -> WAIT when the source base is too thin or
    # untrustworthy. Omitted entirely when evidence_breakdown is empty
    # (e.g., sports_event with match stats). Pure audit layer — does NOT
    # mutate ai_probability, actionable_recommendation, decision_quality,
    # or market_quality. See docs/superpowers/specs/2026-06-30-decision-quality-engine-design.md
    SOURCE_RELIABILITY_ENABLED: bool = _env_bool(
        "SOURCE_RELIABILITY_ENABLED", "false"
    )
    # Overall reliability score below this threshold triggers a YES/NO -> WAIT
    # downgrade. 0.5 = a single failing sub-metric (with the others at 1.0)
    # just barely stays above the threshold.
    SOURCE_RELIABILITY_SCORE_THRESHOLD: float = float(
        os.getenv("SOURCE_RELIABILITY_SCORE_THRESHOLD", "0.5")
    )
    # Minimum fraction of sources classified as official or trusted. Below
    # this ratio, the recommendation is downgraded (too few authoritative
    # sources backing the claim).
    SOURCE_RELIABILITY_MIN_TRUSTED_RATIO: float = float(
        os.getenv("SOURCE_RELIABILITY_MIN_TRUSTED_RATIO", "0.4")
    )
    # Minimum number of distinct domains in the evidence base. Below this,
    # the recommendation is downgraded (single-source echo-chamber risk).
    SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY: int = int(
        os.getenv("SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY", "2")
    )
    # Minimum number of distinct sources. Below this, the recommendation is
    # downgraded (source count too small to support a strong stance).
    SOURCE_RELIABILITY_MIN_SOURCES: int = int(
        os.getenv("SOURCE_RELIABILITY_MIN_SOURCES", "2")
    )
    # ── Source Trust Registry (Plan 4 §6.1) ──────────────────────────
    # When true, source_reliability_service consults the SQLite registry for
    # tier/base-trust overrides. Defaults false for byte-identical pre-Plan-4
    # behavior.
    SOURCE_TRUST_REGISTRY_ENABLED: bool = _env_bool(
        "SOURCE_TRUST_REGISTRY_ENABLED", "false"
    )
    # ── Review Queue (Plan 4 §6.2) ───────────────────────────────────
    # When true, the orchestrator runs pure-function detectors after overlay
    # build and enqueues candidates into review_queue_store. Defaults false
    # for byte-identical pre-Plan-4 behavior.
    REVIEW_QUEUE_ENABLED: bool = _env_bool("REVIEW_QUEUE_ENABLED", "false")
    # Confidence threshold for outcome_prediction_mismatch detector.
    REVIEW_QUEUE_MISMATCH_CONFIDENCE: float = float(
        os.getenv("REVIEW_QUEUE_MISMATCH_CONFIDENCE", "0.75")
    )
    # Plan 5 §5.4: Decision Timeline / Diff Viewer. When enabled, save_events
    # appends an overlay-bearing snapshot of each record to
    # decision_timeline_store so the /decision-timeline route can diff how an
    # event's final direction evolved. Defaults to false → byte-identical to
    # pre-Plan-5 (no snapshot written, no store schema created).
    DECISION_TIMELINE_ENABLED: bool = _env_bool("DECISION_TIMELINE_ENABLED", "false")
    # Phase 5 — LLM Cost and Stability Telemetry (default OFF). When enabled,
    # analyze_event attaches an llm_telemetry overlay block for ALL events
    # (every event makes at least one LLM call or falls back to deterministic).
    # Records degraded_mode (LLM fallback), analysis_quality, real token
    # counts (captured by _ask_ai instrumentation), estimated cost, and
    # sentiment degradation flag. Pure observability layer — does NOT mutate
    # ai_probability, actionable_recommendation, or any decision overlay.
    # See docs/superpowers/specs/2026-06-30-decision-quality-engine-design.md
    LLM_TELEMETRY_ENABLED: bool = _env_bool("LLM_TELEMETRY_ENABLED", "false")
    SCHEDULER_MISFIRE_GRACE_SECONDS: int = int(
        os.getenv("SCHEDULER_MISFIRE_GRACE_SECONDS", "86400")
    )
    SERVER_RELOAD: bool = _env_bool("SERVER_RELOAD", "false")

    # Sentry error tracking (P0-7 §1.2). When SENTRY_DSN is empty, the
    # sentry_sdk wrapper in app.utils.sentry is a no-op so the app boots
    # without a Sentry backend configured. Set the DSN in production to
    # capture FastAPI route exceptions + scheduler job failures.
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")
    SENTRY_ENVIRONMENT: str = os.getenv("SENTRY_ENVIRONMENT", "production")
    SENTRY_RELEASE: str = os.getenv("SENTRY_RELEASE", "")
    # Performance trace sample rate (0.0-1.0). Default 0 = no perf monitoring
    # (keeps P0 minimal; bump to 0.01-0.1 in prod if performance tracing wanted).
    SENTRY_TRACES_SAMPLE_RATE: float = float(
        os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")
    )
    SENTRY_ATTACH_STACKTRACES: bool = _env_bool(
        "SENTRY_ATTACH_STACKTRACES", "true"
    )

    # Restore script health-check probe (used by scripts/restore_stores.py on
    # Windows / non-POSIX systems where fcntl is unavailable). The restore
    # script probes this URL before overwriting live stores; any HTTP
    # response (including 503 degraded) is treated as "service running" so
    # the live DB is never clobbered. Defaults align with the dev server
    # (FastAPI on :8000, /api/health). Operators running behind a proxy or
    # on a non-default port must override both in .env, otherwise the probe
    # falls through to the default and may fail to detect a running service.
    PMRF_HEALTHCHECK_URL: str = os.getenv(
        "PMRF_HEALTHCHECK_URL", "http://localhost:8000/api/health"
    )
    PMRF_HEALTHCHECK_TIMEOUT_SECONDS: float = float(
        os.getenv("PMRF_HEALTHCHECK_TIMEOUT_SECONDS", "5")
    )

    # Strategy-layer Guardrails (P0-8 §6.3 minimum set). When enabled, the
    # post-merge final_displayed_direction is gated by global risk controls
    # (LLM degraded mode, uncalibrated category, high evidence conflict).
    # Default OFF so behavior is byte-identical to pre-guardrail when off.
    # Each guardrail has its own enable flag so operators can pick which
    # controls to enforce.
    GUARDRAILS_ENABLED: bool = _env_bool("GUARDRAILS_ENABLED", "false")
    GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT: bool = _env_bool(
        "GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT", "true"
    )
    GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT: bool = _env_bool(
        "GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT", "true"
    )
    GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT: bool = _env_bool(
        "GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT", "true"
    )
    # conflict_score >= this value triggers the high-conflict guardrail.
    # Default 0.40 matches DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD (low consensus).
    GUARDRAIL_HIGH_CONFLICT_THRESHOLD: float = float(
        os.getenv("GUARDRAIL_HIGH_CONFLICT_THRESHOLD", "0.40")
    )

    # Calibration drift alerts (Plan 2 §1.7). The drift *computation*
    # (ECE, drift_score) is always available and read-only; these flags
    # gate the alert *dispatch* (webhook + Sentry breadcrumb) which has
    # side effects. Default OFF so a fresh install computes drift silently
    # without firing webhooks — byte-identical alert silence to pre-Plan-2.
    DRIFT_ALERTS_ENABLED: bool = _env_bool("DRIFT_ALERTS_ENABLED", "false")
    # Rule 1: recent Brier mean must exceed baseline by this relative
    # threshold (0.30 = 30% worse) to fire brier_relative_drift.
    DRIFT_BRIER_RELATIVE_THRESHOLD: float = float(
        os.getenv("DRIFT_BRIER_RELATIVE_THRESHOLD", "0.30")
    )
    # Rule 2: bucket direction_correct_rate must deviate by more than this
    # many percentage points from baseline to fire bucket_deviation.
    DRIFT_BUCKET_DEVIATION_PP: float = float(
        os.getenv("DRIFT_BUCKET_DEVIATION_PP", "20.0")
    )
    DRIFT_BUCKET_MIN_SAMPLES: int = int(
        os.getenv("DRIFT_BUCKET_MIN_SAMPLES", "2")
    )
    # Number of most-recent scored predictions to treat as the "recent"
    # window for drift comparison.
    DRIFT_RECENT_WINDOW_N: int = int(os.getenv("DRIFT_RECENT_WINDOW_N", "50"))
    # Rule 4: fire scheduler_zero_resolved when this many consecutive
    # successful scheduler runs produce 0 new scored predictions.
    DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS: int = int(
        os.getenv("DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS", "3")
    )
    # Webhook destination for drift alerts. Empty = no webhook (Sentry +
    # log only). When set, POSTs a JSON alert payload on each dispatch.
    DRIFT_ALERT_WEBHOOK_URL: str = os.getenv("DRIFT_ALERT_WEBHOOK_URL", "")
    # Cooldown (seconds) per alert code — prevents webhook spam when the
    # drift condition persists across scrapes. 0 = no cooldown.
    DRIFT_ALERT_COOLDOWN_SECONDS: int = int(
        os.getenv("DRIFT_ALERT_COOLDOWN_SECONDS", "3600")
    )


settings = Settings()
