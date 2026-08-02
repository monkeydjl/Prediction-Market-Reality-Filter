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


def _world_cup_import_enabled_default() -> str:
    return "true" if os.getenv("FOOTBALL_DATA_API_KEY", "").strip() else "false"


def _world_cup_import_mode_default() -> str:
    return "football_data" if os.getenv("FOOTBALL_DATA_API_KEY", "").strip() else "url"


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "deepseek-chat")
    LLM_STARTUP_CHECK_ENABLED: bool = _env_bool(
        "LLM_STARTUP_CHECK_ENABLED", "false"
    )
    LLM_STARTUP_CHECK_TIMEOUT_SECONDS: float = float(
        os.getenv("LLM_STARTUP_CHECK_TIMEOUT_SECONDS", "5.0")
    )

    # Unified LLM Gateway routes. Format:
    # provider:model1,model2|provider2:model3
    # Empty task routes fall back to LLM_ROUTE_DEFAULT, then legacy OPENAI_*.
    LLM_ROUTE_DEFAULT: str = os.getenv("LLM_ROUTE_DEFAULT", "")
    LLM_ROUTE_PROBABILITY_ANALYSIS: str = os.getenv(
        "LLM_ROUTE_PROBABILITY_ANALYSIS", ""
    )
    LLM_ROUTE_TRANSLATION: str = os.getenv("LLM_ROUTE_TRANSLATION", "")
    LLM_ROUTE_OPEN_WEB_EXTRACTION: str = os.getenv(
        "LLM_ROUTE_OPEN_WEB_EXTRACTION", ""
    )
    LLM_ROUTE_CROSS_VALIDATION: str = os.getenv("LLM_ROUTE_CROSS_VALIDATION", "")
    LLM_ROUTE_WORLD_CUP: str = os.getenv("LLM_ROUTE_WORLD_CUP", "")
    LLM_ROUTE_STARTUP_CHECK: str = os.getenv("LLM_ROUTE_STARTUP_CHECK", "")
    LLM_ROUTE_EMBEDDING: str = os.getenv("LLM_ROUTE_EMBEDDING", "")
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
    LLM_MAX_RETRIES_PER_MODEL: int = int(
        os.getenv("LLM_MAX_RETRIES_PER_MODEL", "1")
    )

    LLM_PROVIDER_DEEPSEEK_BASE_URL: str = os.getenv(
        "LLM_PROVIDER_DEEPSEEK_BASE_URL", "https://api.deepseek.com"
    )
    LLM_PROVIDER_DEEPSEEK_API_KEY: str = os.getenv(
        "LLM_PROVIDER_DEEPSEEK_API_KEY", ""
    )
    LLM_PROVIDER_DASHSCOPE_BASE_URL: str = os.getenv(
        "LLM_PROVIDER_DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    LLM_PROVIDER_DASHSCOPE_API_KEY: str = os.getenv(
        "LLM_PROVIDER_DASHSCOPE_API_KEY", ""
    )
    LLM_PROVIDER_OPENAI_BASE_URL: str = os.getenv("LLM_PROVIDER_OPENAI_BASE_URL", "")
    LLM_PROVIDER_OPENAI_API_KEY: str = os.getenv("LLM_PROVIDER_OPENAI_API_KEY", "")
    LLM_PROVIDER_OPENROUTER_BASE_URL: str = os.getenv(
        "LLM_PROVIDER_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )
    LLM_PROVIDER_OPENROUTER_API_KEY: str = os.getenv(
        "LLM_PROVIDER_OPENROUTER_API_KEY", ""
    )

    CORS_ALLOWED_ORIGINS: list[str] = _env_csv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:8000,http://127.0.0.1:8000",
    )
    CORS_ALLOW_CREDENTIALS: bool = _env_bool("CORS_ALLOW_CREDENTIALS", "false")
    CORS_ALLOWED_METHODS: list[str] = _env_csv(
        "CORS_ALLOWED_METHODS",
        "GET,POST,PATCH,DELETE,OPTIONS",
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
    BACKEND_SERVE_FRONTEND: bool = _env_bool("BACKEND_SERVE_FRONTEND", "true")

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
        "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", _world_cup_import_enabled_default()
    )
    WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE: str = os.getenv(
        "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", _world_cup_import_mode_default()
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

    # Legacy Manifold settings are kept only so existing .env files do not break
    # startup. Manifold is no longer an active discovery or auto-resolution
    # source.
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

    LIMITLESS_SOURCE_ENABLED: bool = _env_bool("LIMITLESS_SOURCE_ENABLED", "true")
    LIMITLESS_API_URL: str = os.getenv(
        "LIMITLESS_API_URL",
        "https://api.limitless.exchange/markets/active",
    )
    LIMITLESS_SOURCE_NAME: str = os.getenv("LIMITLESS_SOURCE_NAME", "Limitless")

    OPINION_SOURCE_ENABLED: bool = _env_bool("OPINION_SOURCE_ENABLED", "true")
    OPINION_API_URL: str = os.getenv(
        "OPINION_API_URL",
        "https://openapi.opinion.trade/openapi/market",
    )
    OPINION_API_KEY: str = os.getenv("OPINION_API_KEY", "")
    OPINION_SOURCE_NAME: str = os.getenv("OPINION_SOURCE_NAME", "Opinion")

    PREDICT_FUN_SOURCE_ENABLED: bool = _env_bool("PREDICT_FUN_SOURCE_ENABLED", "true")
    PREDICT_FUN_API_URL: str = os.getenv(
        "PREDICT_FUN_API_URL",
        "https://api.predict.fun/v1/markets",
    )
    PREDICT_FUN_API_KEY: str = os.getenv("PREDICT_FUN_API_KEY", "")
    PREDICT_FUN_SOURCE_NAME: str = os.getenv(
        "PREDICT_FUN_SOURCE_NAME",
        "Predict.fun",
    )

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

    # Football-Data.org - default World Cup fixture sync source
    FOOTBALL_DATA_API_KEY: str = os.getenv("FOOTBALL_DATA_API_KEY", "")
    FOOTBALL_DATA_BASE_URL: str = os.getenv(
        "FOOTBALL_DATA_BASE_URL",
        "https://api.football-data.org/v4",
    )

    # Football live weather forecast fill (P1-F7). Optional: when
    # FOOTBALL_LIVE_WEATHER_URL is empty (default) the adapter behaves exactly
    # as before (env override → static climate). The default URL template
    # targets keyless Open-Meteo; FOOTBALL_LIVE_WEATHER_API_KEY is appended as
    # `apikey` only when set. Response is normalized to weather_temp_c /
    # weather_condition. Live fetch is attempted only within the kickoff
    # horizon and cached in-memory for the TTL. No mandatory config.
    FOOTBALL_LIVE_WEATHER_URL: str = os.getenv("FOOTBALL_LIVE_WEATHER_URL", "")
    FOOTBALL_LIVE_WEATHER_API_KEY: str = os.getenv("FOOTBALL_LIVE_WEATHER_API_KEY", "")
    FOOTBALL_LIVE_WEATHER_TIMEOUT_S: float = float(
        os.getenv("FOOTBALL_LIVE_WEATHER_TIMEOUT_S", "5.0")
    )
    FOOTBALL_LIVE_WEATHER_HORIZON_HOURS: float = float(
        os.getenv("FOOTBALL_LIVE_WEATHER_HORIZON_HOURS", "72.0")
    )
    FOOTBALL_LIVE_WEATHER_CACHE_TTL_HOURS: float = float(
        os.getenv("FOOTBALL_LIVE_WEATHER_CACHE_TTL_HOURS", "6.0")
    )

    # The Odds API - Betting odds data source
    # Free tier: 500 requests/month
    # Register at: https://the-odds-api.com/
    ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
    ODDS_API_BASE_URL: str = os.getenv(
        "ODDS_API_BASE_URL",
        "https://api.the-odds-api.com/v4",
    )
    ODDS_API_ENABLED: bool = _env_bool("ODDS_API_ENABLED", "false")

    # Multi-model cross-validation: an independent second model re-estimates the
    # probability for the same question + evidence, surfaced as agreement /
    # divergence. Prefer LLM_ROUTE_CROSS_VALIDATION; legacy CROSS_VALIDATION_*
    # fields remain as compatibility/enablement flags.
    CROSS_VALIDATION_MODEL: str = os.getenv("CROSS_VALIDATION_MODEL", "")
    CROSS_VALIDATION_BASE_URL: str = os.getenv("CROSS_VALIDATION_BASE_URL", "")
    CROSS_VALIDATION_API_KEY: str = os.getenv("CROSS_VALIDATION_API_KEY", "")

    # Legacy translation fields are retained for explicit client injection paths;
    # the default translation path uses LLM_ROUTE_TRANSLATION / Gateway fallback.
    TRANSLATION_MODEL: str = os.getenv("TRANSLATION_MODEL", "")
    TRANSLATION_BASE_URL: str = os.getenv("TRANSLATION_BASE_URL", "")
    TRANSLATION_API_KEY: str = os.getenv("TRANSLATION_API_KEY", "")

    # Open-web structured event extraction: turn collected articles into native
    # candidate events (forward-looking questions), not just evidence. Prefer
    # LLM_ROUTE_OPEN_WEB_EXTRACTION; OPEN_WEB_EXTRACTION_MODEL remains as the
    # legacy enablement flag.
    OPEN_WEB_EXTRACTION_MODEL: str = os.getenv("OPEN_WEB_EXTRACTION_MODEL", "")
    OPEN_WEB_ENABLED: bool = _env_bool("OPEN_WEB_ENABLED", "false")
    OPEN_WEB_SOURCE_NAME: str = os.getenv("OPEN_WEB_SOURCE_NAME", "Open Web")

    # Semantic news relevance via embeddings. Prefer LLM_ROUTE_EMBEDDING for
    # multi-provider/model fallback. Legacy EMBEDDING_MODEL remains as an
    # explicit single-provider route. The default chat provider (DeepSeek) has
    # NO embeddings endpoint, so point EMBEDDING_BASE_URL / EMBEDDING_API_KEY at
    # a provider that does (DashScope, OpenAI, ...). When base_url is empty the
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
    # Sports recommendation allocation cap (% of bankroll, P1-SB3)
    SPORT_REC_MAX_ALLOCATION_PCT: float = float(
        os.getenv("SPORT_REC_MAX_ALLOCATION_PCT", "2.0")
    )
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
    # Per-source weight multipliers for discovery.  Each active source is asked
    # for ``limit * weight`` candidates instead of the flat ``limit``.
    # Polymarket is the primary prediction-market source (highest weight),
    # Kalshi is secondary, and verified on-chain sources are lower-priority
    # discovery inputs. Open Web (LLM extraction from news) is kept below market
    # sources so the event mix is dominated by real market prices, not
    # news-derived speculation.
    SOURCE_WEIGHTS: dict[str, float] = {
        "Polymarket": float(os.getenv("SOURCE_WEIGHT_POLYMARKET", "3.0")),
        "Kalshi": float(os.getenv("SOURCE_WEIGHT_KALSHI", "1.0")),
        "Limitless": float(os.getenv("SOURCE_WEIGHT_LIMITLESS", "0.8")),
        "Opinion": float(os.getenv("SOURCE_WEIGHT_OPINION", "0.6")),
        "Predict.fun": float(os.getenv("SOURCE_WEIGHT_PREDICT_FUN", "0.5")),
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
    # Confidence threshold for auto_resolve_low_confidence detector.
    REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE: float = float(
        os.getenv("REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE", "0.95")
    )
    # Conclusion challenge gate. Disabled by default; when enabled, the event
    # and World Cup orchestrators run a post-conclusion "negative check" before
    # strong outputs are published.
    CONCLUSION_CHALLENGE_ENABLED: bool = _env_bool(
        "CONCLUSION_CHALLENGE_ENABLED", "false"
    )
    CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED: bool = _env_bool(
        "CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED", "false"
    )
    CONCLUSION_CHALLENGE_STRICTNESS: str = os.getenv(
        "CONCLUSION_CHALLENGE_STRICTNESS", "normal"
    )
    CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS: int = int(
        os.getenv("CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS", "1")
    )
    WORLD_CUP_CHALLENGE_ENABLED: bool = _env_bool(
        "WORLD_CUP_CHALLENGE_ENABLED", "false"
    )
    EVENT_CHALLENGE_ENABLED: bool = _env_bool("EVENT_CHALLENGE_ENABLED", "false")
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

    # ── Scheduler failure alerts (E8) ──
    # The scheduler already records failures in the loop-run ledger,
    # increments the SCHEDULER_FAILED_RUNS Prometheus counter, and
    # forwards exceptions to Sentry via _finish_run. These flags gate
    # an *additional* best-effort notification dispatcher (webhook +
    # Sentry breadcrumb + structured log) with per-job cooldown. Default
    # OFF so a fresh install is byte-identical to pre-E8.
    SCHEDULER_FAILURE_ALERT_ENABLED: bool = _env_bool(
        "SCHEDULER_FAILURE_ALERT_ENABLED", "false"
    )
    SCHEDULER_FAILURE_ALERT_WEBHOOK_URL: str = os.getenv(
        "SCHEDULER_FAILURE_ALERT_WEBHOOK_URL", ""
    )
    SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS: int = int(
        os.getenv("SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS", "1800")
    )

    # ── Quality alerts (LATER #3) — slice-threshold alerting on quality report ──
    QUALITY_ALERT_MIN_SAMPLES: int = int(os.getenv("QUALITY_ALERT_MIN_SAMPLES", "10"))
    QUALITY_ALERT_DIRECTION_ACCURACY_MEDIUM: float = float(os.getenv("QUALITY_ALERT_DIRECTION_ACCURACY_MEDIUM", "0.60"))
    QUALITY_ALERT_DIRECTION_ACCURACY_HIGH: float = float(os.getenv("QUALITY_ALERT_DIRECTION_ACCURACY_HIGH", "0.50"))
    QUALITY_ALERT_BRIER_MEDIUM: float = float(os.getenv("QUALITY_ALERT_BRIER_MEDIUM", "0.25"))
    QUALITY_ALERT_BRIER_HIGH: float = float(os.getenv("QUALITY_ALERT_BRIER_HIGH", "0.35"))
    QUALITY_ALERT_MISSING_CALIBRATION_RATE_MEDIUM: float = float(os.getenv("QUALITY_ALERT_MISSING_CALIBRATION_RATE_MEDIUM", "0.20"))
    QUALITY_ALERT_MISSING_CALIBRATION_RATE_HIGH: float = float(os.getenv("QUALITY_ALERT_MISSING_CALIBRATION_RATE_HIGH", "0.40"))
    QUALITY_ALERT_REPORT_ERRORS_HIGH: int = int(os.getenv("QUALITY_ALERT_REPORT_ERRORS_HIGH", "1"))

    # ── Domain reliability tracking (LATER #2) — source trust feedback loop ──
    DOMAIN_RELIABILITY_TRACKING_ENABLED: bool = _env_bool(
        "DOMAIN_RELIABILITY_TRACKING_ENABLED", "false"
    )
    DOMAIN_RELIABILITY_DB_PATH: str = os.getenv(
        "DOMAIN_RELIABILITY_DB_PATH",
        os.path.join(
            os.path.dirname(__file__), "..", "..", "domain_reliability.db"
        ),
    )
    DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES: int = int(
        os.getenv("DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES", "5")
    )
    # Domain reliability feedback (LATER #2 v2): feed per-domain historical
    # accuracy back into build_source_reliability as a layered prior.
    DOMAIN_RELIABILITY_FEEDBACK_ENABLED: bool = _env_bool(
        "DOMAIN_RELIABILITY_FEEDBACK_ENABLED", "false"
    )
    DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT: int = int(
        os.getenv("DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT", "5")
    )

    # Prediction Kernel feature flag (default OFF). When false, the
    # /api/predictions/* routes return 503 Service Unavailable (except
    # /engines which returns a static list). Set KERNEL_PREDICTION_ENABLED=true
    # to enable the full prediction pipeline through the PredictionKernel.
    KERNEL_PREDICTION_ENABLED: bool = _env_bool(
        "KERNEL_PREDICTION_ENABLED", "false"
    )

    # Football multi-factor engine (default OFF). When true and Kernel is
    # enabled, registers FootballMultiFactorEngine (elo/odds/form/rest/
    # injury/h2h) alongside EloOddsEngine. Call predict with
    # engine=football_multi_factor, or use engine=auto after enough samples.
    # Does not change EloOddsEngine behavior.
    FOOTBALL_MULTI_FACTOR_ENGINE_ENABLED: bool = _env_bool(
        "FOOTBALL_MULTI_FACTOR_ENGINE_ENABLED", "false"
    )

    # Kernel Dixon-Coles engine (default OFF). Elo → xG → Poisson + rho.
    # Requires KERNEL_PREDICTION_ENABLED. Rho from data/dixon_coles_params.json.
    DIXON_COLES_ENGINE_ENABLED: bool = _env_bool(
        "DIXON_COLES_ENGINE_ENABLED", "false"
    )

    # Kernel GBM engine adapter (default OFF). Wraps legacy LightGBM xG
    # models; falls back to Elo baseline when models are missing.
    GBM_ENGINE_ENABLED: bool = _env_bool(
        "GBM_ENGINE_ENABLED", "false"
    )

    # Inverse-Brier ensemble over registered football engines (default OFF).
    # When true, registers engine name "ensemble" that fuses children present
    # in the registry (elo_odds + multi-factor/dc/gbm when enabled).
    FOOTBALL_ENSEMBLE_ENGINE_ENABLED: bool = _env_bool(
        "FOOTBALL_ENSEMBLE_ENGINE_ENABLED", "false"
    )

    # Kernel situational engine (default OFF). Soft post-adjustment for
    # knockout / must-win / group status on top of EloOdds (or another base).
    # Register as engine name "situational". Does not hardcode large PP shifts.
    SITUATIONAL_ENGINE_ENABLED: bool = _env_bool(
        "SITUATIONAL_ENGINE_ENABLED", "false"
    )

    # Phase 2 — Multi-league support (default OFF). When false, only
    # World Cup (wc- prefix) adapters are registered. Set to true to
    # enable UCL and EPL adapters.
    PHASE2_LEAGUES_ENABLED: bool = _env_bool(
        "PHASE2_LEAGUES_ENABLED", "false"
    )

    # Phase 3 — Unified learning loop (default OFF). When false,
    # process_outcome only records outcomes and computes errors
    # (existing Phase 1 behavior). Set to true to enable calibration,
    # weight updates, and engine score persistence.
    PHASE3_LEARNING_ENABLED: bool = _env_bool(
        "PHASE3_LEARNING_ENABLED", "false"
    )
    LEARNING_WINDOW_SIZE: int = int(
        os.getenv("LEARNING_WINDOW_SIZE", "30")
    )
    EWMA_ALPHA: float = float(
        os.getenv("EWMA_ALPHA", "0.1")
    )
    MIN_SAMPLES_FOR_CALIBRATION: int = int(
        os.getenv("MIN_SAMPLES_FOR_CALIBRATION", "10")
    )
    MIN_SAMPLES_FOR_ENGINE_SELECT: int = int(
        os.getenv("MIN_SAMPLES_FOR_ENGINE_SELECT", "5")
    )
    # P1-V5: apply confidence-bucket linear calibration on Kernel.predict
    # (default OFF — keep raw engine probs until buckets have real samples).
    KERNEL_CONDITIONAL_CALIBRATION_ENABLED: bool = _env_bool(
        "KERNEL_CONDITIONAL_CALIBRATION_ENABLED", "false"
    )
    WEIGHT_FLOOR: float = float(
        os.getenv("WEIGHT_FLOOR", "0.05")
    )
    WEIGHT_CEILING: float = float(
        os.getenv("WEIGHT_CEILING", "0.95")
    )

    # ClubElo.com service configuration
    CLUB_ELO_CACHE_TTL_DAYS: int = int(
        os.getenv("CLUB_ELO_CACHE_TTL_DAYS", "7")
    )
    CLUB_ELO_REQUEST_INTERVAL: float = float(
        os.getenv("CLUB_ELO_REQUEST_INTERVAL", "1.0")
    )

    # Phase 4 — NBA Integration (default OFF). When false, nba- prefix
    # match_ids return 404 and NBAAdapter/BasketballEngine are not
    # instantiated.
    PHASE4_NBA_ENABLED: bool = _env_bool("PHASE4_NBA_ENABLED", "false")
    BALLDONTLIE_API_KEY: str = os.getenv("BALLDONTLIE_API_KEY", "")
    NBA_ELO_HFA: int = int(os.getenv("NBA_ELO_HFA", "100"))
    # Playoff HFA slightly lower (better travel/rest parity) — P1-B5
    NBA_ELO_HFA_PLAYOFF: int = int(os.getenv("NBA_ELO_HFA_PLAYOFF", "90"))
    NBA_HOME_COURT_PLAYOFF: float = float(os.getenv("NBA_HOME_COURT_PLAYOFF", "0.55"))
    NBA_ELO_K_REGULAR: int = int(os.getenv("NBA_ELO_K_REGULAR", "20"))
    NBA_ELO_K_PLAYOFF: int = int(os.getenv("NBA_ELO_K_PLAYOFF", "30"))
    NBA_LEAGUE_AVG_TOTAL: float = float(os.getenv("NBA_LEAGUE_AVG_TOTAL", "220.0"))

    # LoL esports (ADR-004/005) — default OFF; no production API until
    # docs/dev/lol/GATES.md P2/P3/P6. Vendor env names are frozen (D4);
    # setting grid/pandascore does NOT enable HTTP without a client + legal.
    PHASE_LOL_ENABLED: bool = _env_bool("PHASE_LOL_ENABLED", "false")
    LOL_DRY_RUN_IMPORT: bool = _env_bool("LOL_DRY_RUN_IMPORT", "false")
    LOL_DRY_RUN_FIXTURES_PATH: str = os.getenv(
        "LOL_DRY_RUN_FIXTURES_PATH", ""
    ).strip()
    # null | dry_run | grid | pandascore (unknown → null)
    LOL_SCHEDULE_VENDOR: str = (
        os.getenv("LOL_SCHEDULE_VENDOR", "null").strip().lower() or "null"
    )
    LOL_VENDOR_API_BASE: str = os.getenv("LOL_VENDOR_API_BASE", "").strip()
    # Secret store / env only — never return this string from public APIs.
    LOL_VENDOR_API_KEY: str = os.getenv("LOL_VENDOR_API_KEY", "").strip()
    # ADR-005 D6: hours before operator may import signed settle JSON.
    LOL_SETTLE_GRACE_HOURS: int = max(
        0, int(os.getenv("LOL_SETTLE_GRACE_HOURS", "6") or "6")
    )

    # Phase 5 — MLB/NHL Integration (default OFF). When false, mlb-/nhl-
    # prefix match_ids return 404 and MLB/NHL components are not
    # instantiated. MLB/NHL stats APIs require no API key (graceful
    # degradation when unreachable: sync_schedule returns 0).
    PHASE5_MLB_ENABLED: bool = _env_bool("PHASE5_MLB_ENABLED", "false")
    PHASE5_NHL_ENABLED: bool = _env_bool("PHASE5_NHL_ENABLED", "false")

    # MLB Elo parameters (self-computed from historical games)
    MLB_ELO_HFA: int = int(os.getenv("MLB_ELO_HFA", "50"))
    MLB_ELO_K_REGULAR: int = int(os.getenv("MLB_ELO_K_REGULAR", "20"))
    MLB_ELO_K_PLAYOFF: int = int(os.getenv("MLB_ELO_K_PLAYOFF", "30"))
    MLB_ELO_SEASON_CARRY: float = float(os.getenv("MLB_ELO_SEASON_CARRY", "0.7"))
    MLB_LEAGUE_AVG_TOTAL: float = float(os.getenv("MLB_LEAGUE_AVG_TOTAL", "8.5"))

    # NHL Elo parameters
    NHL_ELO_HFA: int = int(os.getenv("NHL_ELO_HFA", "55"))
    NHL_ELO_K_REGULAR: int = int(os.getenv("NHL_ELO_K_REGULAR", "20"))
    NHL_ELO_K_PLAYOFF: int = int(os.getenv("NHL_ELO_K_PLAYOFF", "30"))
    NHL_ELO_SEASON_CARRY: float = float(os.getenv("NHL_ELO_SEASON_CARRY", "0.75"))
    NHL_LEAGUE_AVG_TOTAL: float = float(os.getenv("NHL_LEAGUE_AVG_TOTAL", "5.5"))

    # Phase 7 — Sport Market Bridge (default OFF). Connects the Sports
    # Prediction Kernel with Polymarket + The Odds API to produce verified
    # market-implied probabilities per match outcome. When the master flag is
    # false, all new endpoints return 503 and collection tasks are not
    # scheduled.
    PHASE7_SPORT_MARKET_BRIDGE_ENABLED: bool = _env_bool(
        "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", "false"
    )
    PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED: bool = _env_bool(
        "PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED", "false"
    )
    PHASE7_ODDS_API_MULTI_LEAGUE_ENABLED: bool = _env_bool(
        "PHASE7_ODDS_API_MULTI_LEAGUE_ENABLED", "false"
    )
    PHASE7_SPORT_MARKET_BRIDGE_SCHEDULER_ENABLED: bool = _env_bool(
        "PHASE7_SPORT_MARKET_BRIDGE_SCHEDULER_ENABLED", "false"
    )
    PHASE7_POLYMARKET_SPORTS_FETCH_INTERVAL_SECONDS: int = int(
        os.getenv("PHASE7_POLYMARKET_SPORTS_FETCH_INTERVAL_SECONDS", "600")
    )
    PHASE7_SPORT_MARKET_LINK_PENDING_THRESHOLD: float = float(
        os.getenv("PHASE7_SPORT_MARKET_LINK_PENDING_THRESHOLD", "0.6")
    )
    # P1-V2: auto-verify pending links above this confidence (default OFF)
    PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_ENABLED: bool = _env_bool(
        "PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_ENABLED", "false"
    )
    PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_THRESHOLD: float = float(
        os.getenv("PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_THRESHOLD", "0.95")
    )
    # Scheduler interval flags used by the sport-market-bridge scheduler jobs
    # (Task 6). Defaults match the brief's documented cadence: hourly
    # Polymarket discovery, 6-hourly traditional-odds fetch, 1-minute snapshot
    # capture. All three are only consumed when
    # PHASE7_SPORT_MARKET_BRIDGE_ENABLED=true (gated inside start_scheduler).
    POLYMARKET_SPORTS_DISCOVERY_INTERVAL_MIN: int = int(
        os.getenv("POLYMARKET_SPORTS_DISCOVERY_INTERVAL_MIN", "60")
    )
    ODDS_FETCH_INTERVAL_MIN: int = int(
        os.getenv("ODDS_FETCH_INTERVAL_MIN", "10")
    )
    MARKET_SNAPSHOT_INTERVAL_MIN: int = int(
        os.getenv("MARKET_SNAPSHOT_INTERVAL_MIN", "1")
    )
    # Phase 7 Subproject B — Edge Detector (default OFF). Computes
    # model-vs-market divergence per outcome for sports matches. When false,
    # all /api/sport-edges/* endpoints return 503 and the scheduler job is
    # not registered.
    PHASE7_EDGE_DETECTOR_ENABLED: bool = _env_bool(
        "PHASE7_EDGE_DETECTOR_ENABLED", "false"
    )
    EDGE_DETECTION_INTERVAL_MIN: int = int(
        os.getenv("EDGE_DETECTION_INTERVAL_MIN", "5")
    )
    # Phase 7 Subproject C — Sport Recommendation Engine (default OFF).
    # Stateless service that computes SportActionableRecommendation from B's
    # persisted edges. When false, all /api/sport-recommendations/* endpoints
    # return 503.
    PHASE7_SPORT_RECOMMENDATION_ENABLED: bool = _env_bool(
        "PHASE7_SPORT_RECOMMENDATION_ENABLED", "false"
    )

    # Phase 7 Subproject D — Market Settlement Feedback (default OFF).
    # When false, all /api/sport-settlements/* endpoints return 503 and the
    # scheduler job is not registered.
    PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED: bool = _env_bool(
        "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED", "false"
    )
    PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED: bool = _env_bool(
        "PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED", "false"
    )
    MARKET_SETTLEMENT_INTERVAL_MIN: int = int(
        os.getenv("MARKET_SETTLEMENT_INTERVAL_MIN", "10")
    )
    MARKET_SETTLEMENT_BATCH_LIMIT: int = int(
        os.getenv("MARKET_SETTLEMENT_BATCH_LIMIT", "50")
    )
    MIN_SAMPLES_FOR_MARKET_CALIBRATION: int = int(
        os.getenv("MIN_SAMPLES_FOR_MARKET_CALIBRATION", "10")
    )
    MARKET_CALIBRATION_WINDOW_SIZE: int = int(
        os.getenv("MARKET_CALIBRATION_WINDOW_SIZE", "30")
    )

    # Phase 8 — Calibration Fusion (default OFF). When true,
    # EdgeDetectorService._compute_trust delegates to
    # CalibrationFusionService which reads both Phase 3's
    # KernelCalibration and Phase 7 D's KernelMarketCalibration to
    # compute a sample-count-weighted composite trust. When false
    # (default), _compute_trust falls back to Phase 7 Phase-3-only
    # behavior — zero-invasion.
    PHASE8_CALIBRATION_FUSION_ENABLED: bool = _env_bool(
        "PHASE8_CALIBRATION_FUSION_ENABLED", "false"
    )

    # === Phase 9 — Accuracy Sprint ===
    PHASE9_ACCURACY_SPRINT_ENABLED: bool = _env_bool("PHASE9_ACCURACY_SPRINT_ENABLED", "false")
    PHASE9_LEARNING_ACTIVATED: bool = _env_bool("PHASE9_LEARNING_ACTIVATED", "false")
    PHASE9_OPTIMIZATION_TRIALS: int = int(os.getenv("PHASE9_OPTIMIZATION_TRIALS", "150"))
    PHASE9_BACKTEST_SEASONS: str = os.getenv("PHASE9_BACKTEST_SEASONS", "2023-24,2024-25")
    PHASE9_OPTIMIZATION_INTERVAL_MIN: int = int(os.getenv("PHASE9_OPTIMIZATION_INTERVAL_MIN", "0"))
    PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN: int = int(os.getenv("PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN", "0"))

    # === Phase 10 — Real-Time Price Push ===
    PHASE10_REALTIME_PUSH_ENABLED: bool = _env_bool("PHASE10_REALTIME_PUSH_ENABLED", "false")
    WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS: int = int(os.getenv("WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS", "30"))
    WEBSOCKET_MAX_RECONNECT_DELAY_SECONDS: int = int(os.getenv("WEBSOCKET_MAX_RECONNECT_DELAY_SECONDS", "30"))

    # === Phase 11 — Kalshi Sports Market Integration ===
    PHASE11_KALSHI_SPORTS_ENABLED: bool = _env_bool("PHASE11_KALSHI_SPORTS_ENABLED", "false")
    KALSHI_SPORTS_FETCH_INTERVAL_SECONDS: int = int(os.getenv("KALSHI_SPORTS_FETCH_INTERVAL_SECONDS", "600"))
    KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS: float = float(os.getenv("KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS", "1.0"))

    # === Phase 12 — Futures/Championship Markets ===
    PHASE12_FUTURES_MARKETS_ENABLED: bool = _env_bool("PHASE12_FUTURES_MARKETS_ENABLED", "false")
    FUTURES_DISCOVERY_INTERVAL_MIN: int = int(os.getenv("FUTURES_DISCOVERY_INTERVAL_MIN", "60"))
    FUTURES_SNAPSHOT_INTERVAL_MIN: int = int(os.getenv("FUTURES_SNAPSHOT_INTERVAL_MIN", "5"))


settings = Settings()
