# World Cup Prediction Module - Configuration Guide

## Overview

The World Cup prediction module integrates multiple data sources and prediction engines to provide real-time match predictions with dynamic factor weighting.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Prediction Pipeline                       │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐     ┌──────────────┐                      │
│  │ API-Football │────▶│ Match Fixture│                      │
│  │   Sync       │     │    Storage   │                      │
│  └──────────────┘     └──────────────┘                      │
│         │                     │                              │
│         ▼                     ▼                              │
│  ┌──────────────────────────────────────┐                   │
│  │       Enhanced Data Layer            │                   │
│  │  ┌────────────┐  ┌────────────────┐ │                   │
│  │  │   Market   │  │   Sentiment    │ │                   │
│  │  │   Value    │  │   Analysis     │ │                   │
│  │  │(Transfermkt)│  │ (News+Reddit) │ │                   │
│  │  └────────────┘  └────────────────┘ │                   │
│  │  ┌─────────────────────────────────┐│                   │
│  │  │   Betting Odds (The Odds API)   ││                   │
│  │  │      with smart caching          ││                   │
│  │  └─────────────────────────────────┘│                   │
│  └──────────────────────────────────────┘                   │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────────────────────────────┐                   │
│  │      Prediction Engines              │                   │
│  │  ┌──────────────┐  ┌──────────────┐ │                   │
│  │  │  Elo + Odds  │  │    Hybrid    │ │                   │
│  │  │    Fusion    │  │   AI Model   │ │                   │
│  │  └──────────────┘  └──────────────┘ │                   │
│  └──────────────────────────────────────┘                   │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────────────────────────────┐                   │
│  │    Prediction Storage & History      │                   │
│  └──────────────────────────────────────┘                   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Required Environment Variables

### Core Configuration

```bash
# OpenAI API (required for AI predictions)
OPENAI_API_KEY=sk-...

# API-Football (required for fixture sync)
API_FOOTBALL_KEY=your-api-football-key
API_FOOTBALL_BASE_URL=https://v3.football.api-sports.io

# The Odds API (required for betting odds)
ODDS_API_KEY=your-odds-api-key
ODDS_API_BASE_URL=https://api.the-odds-api.com/v4

# Database
DATABASE_URL=sqlite:///./prediction.db  # or PostgreSQL for production
```

### Optional Enhancements

```bash
# Transfermarkt scraping (market value data)
# No API key needed - web scraping based
TRANSFERMARKT_RATE_LIMIT_DELAY=2.0  # seconds between requests

# Reddit API (sentiment analysis)
REDDIT_CLIENT_ID=your-reddit-client-id
REDDIT_CLIENT_SECRET=your-reddit-client-secret
REDDIT_USER_AGENT=WorldCupPredictor/1.0

# News API (sentiment analysis)
NEWS_API_KEY=your-news-api-key
```

## API Quota Management

### The Odds API (Free Tier: 500 requests/month)

**Cache Strategy:**
- Pre-match odds: 1 hour TTL
- Live match odds: 5 minutes TTL
- Historical cache lookup before API call
- Estimated savings: ~80% of potential API calls

**Quota Monitoring:**
```bash
# Check remaining quota
curl http://localhost:8000/api/analytics/odds-cache-stats
```

### API-Football (depends on plan)

**Sync Strategy:**
- Fixture sync: Once per day (automated via scheduler)
- Manual sync: POST /api/world-cup/predictions/sync-fixtures
- Live updates: On-demand prediction triggers

**Rate Limiting:**
- Implemented via InMemoryRateLimitMiddleware
- Default: 100 requests per hour per IP

## Prediction Factor Weights

### Elo + Odds Engine

```python
FACTOR_WEIGHTS = {
    "elo_rating": 0.35,          # Historical performance
    "recent_form": 0.25,         # Last 5 matches
    "betting_odds": 0.40,        # Market consensus
}
```

### Hybrid Engine (with enhanced data)

```python
FACTOR_WEIGHTS = {
    # Core factors (70%)
    "elo_rating": 0.25,          # 25%
    "recent_form": 0.20,         # 20%
    "betting_odds": 0.25,        # 25%
    
    # Enhanced factors (30%)
    "market_value": 0.15,        # 15% - Squad valuation
    "sentiment": 0.10,           # 10% - Public sentiment
    "head_to_head": 0.05,        # 5%  - Historical matchups
}
```

**Graceful Degradation:**
- If enhanced data unavailable, weights redistribute to core factors
- Minimum confidence threshold: 0.4
- Default neutral ratings for missing data

## Cache Configuration

### Market Value Cache
- **TTL:** 7 days
- **Storage:** `team_market_values` table
- **Source:** Transfermarkt web scraping
- **Update strategy:** Lazy refresh on prediction request

### Sentiment Cache
- **TTL:** 6 hours
- **Storage:** `team_sentiment` table
- **Sources:** Reddit + News API
- **Update strategy:** Lazy refresh on prediction request

### Odds Cache
- **TTL:** 1 hour (pre-match), 5 minutes (live)
- **Storage:** `odds_cache` table
- **Source:** The Odds API
- **Expiry field:** `cached_at` + TTL

## Database Schema

### Core Tables

```sql
-- Match fixtures from API-Football
CREATE TABLE match_fixtures (
    match_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    kickoff_utc DATETIME NOT NULL,
    venue TEXT,
    stage TEXT NOT NULL,
    group TEXT,
    status TEXT DEFAULT 'scheduled'
);

-- Current predictions
CREATE TABLE match_predictions (
    match_id TEXT PRIMARY KEY,
    predicted_home_score REAL NOT NULL,
    predicted_away_score REAL NOT NULL,
    home_win_prob REAL NOT NULL,
    draw_prob REAL NOT NULL,
    away_win_prob REAL NOT NULL,
    confidence REAL NOT NULL,
    prediction_method TEXT DEFAULT 'hybrid',
    factors JSON,
    last_updated DATETIME NOT NULL
);

-- Prediction history (time-series)
CREATE TABLE prediction_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    timestamp DATETIME NOT NULL,
    predicted_home_score REAL NOT NULL,
    predicted_away_score REAL NOT NULL,
    confidence REAL NOT NULL,
    trigger TEXT
);

-- Match results for accuracy tracking
CREATE TABLE match_results (
    match_id TEXT PRIMARY KEY,
    final_home_score INTEGER NOT NULL,
    final_away_score INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    score_mae REAL,
    outcome_correct INTEGER,
    brier_score REAL
);
```

### Enhanced Data Tables

```sql
-- Squad market values from Transfermarkt
CREATE TABLE team_market_values (
    team_name TEXT PRIMARY KEY,
    total_market_value REAL NOT NULL,  -- millions €
    avg_player_value REAL NOT NULL,
    num_players INTEGER NOT NULL,
    scraped_at DATETIME NOT NULL
);

-- Team sentiment from news + social media
CREATE TABLE team_sentiment (
    team_name TEXT PRIMARY KEY,
    overall_sentiment REAL NOT NULL,    -- -1 to 1
    news_sentiment REAL NOT NULL,
    reddit_sentiment REAL NOT NULL,
    confidence REAL NOT NULL,           -- 0 to 1
    article_count INTEGER NOT NULL,
    scraped_at DATETIME NOT NULL
);

-- Odds cache
CREATE TABLE odds_cache (
    match_key TEXT PRIMARY KEY,
    home_odds REAL NOT NULL,
    draw_odds REAL NOT NULL,
    away_odds REAL NOT NULL,
    source TEXT NOT NULL,
    cached_at DATETIME NOT NULL
);
```

## API Endpoints

### Prediction API

```bash
# Initialize database tables
POST /api/world-cup/predictions/init-db

# Sync fixtures from API-Football
POST /api/world-cup/predictions/sync-fixtures

# Get all matches
GET /api/world-cup/predictions/matches?stage=GROUP_STAGE&status=scheduled

# Get single match with prediction
GET /api/world-cup/predictions/matches/{match_id}

# Trigger prediction (manual)
POST /api/world-cup/predictions/matches/{match_id}/predict
Body: {"engine": "elo_odds" | "hybrid" | "auto"}

# Batch predict upcoming matches
POST /api/world-cup/predictions/batch-predict

# Get today's matches
GET /api/world-cup/predictions/today

# Get prediction history timeline
GET /api/world-cup/predictions/matches/{match_id}/prediction-history
```

### Analytics API

```bash
# Engine usage statistics
GET /api/analytics/engine-stats

# Prediction accuracy metrics
GET /api/analytics/accuracy-stats

# Odds cache performance
GET /api/analytics/odds-cache-stats

# Prediction timeline for a match
GET /api/analytics/prediction-timeline?match_id={id}

# System health check
GET /api/analytics/system-health
```

## Deployment Checklist

### Pre-deployment

- [ ] Set all required environment variables
- [ ] Verify API keys have sufficient quota
- [ ] Run database migrations: `POST /api/world-cup/predictions/init-db`
- [ ] Sync initial fixtures: `POST /api/world-cup/predictions/sync-fixtures`
- [ ] Test prediction engine: `POST /api/world-cup/predictions/matches/{match_id}/predict`
- [ ] Verify frontend can fetch predictions: `GET /api/world-cup/predictions/today`

### Production Configuration

```bash
# FastAPI settings
ENVIRONMENT=production
DEBUG=False

# CORS for production frontend
CORS_ALLOWED_ORIGINS=https://your-domain.com

# Database (PostgreSQL recommended)
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Scheduler (enable for automated fixture sync)
SCHEDULER_ENABLED=true

# Security
API_WRITE_KEY=your-secure-write-key  # Protect write endpoints
```

### Performance Tuning

```python
# Cache TTLs (in seconds)
ODDS_CACHE_TTL_PREMATCH = 3600        # 1 hour
ODDS_CACHE_TTL_LIVE = 300             # 5 minutes
MARKET_VALUE_TTL_DAYS = 7             # 1 week
SENTIMENT_TTL_HOURS = 6               # 6 hours

# Rate limits
RATE_LIMIT_REQUESTS_PER_HOUR = 100    # Per IP
ODDS_API_DAILY_LIMIT = 16             # 500/month ≈ 16/day

# Batch prediction
BATCH_PREDICT_LOOKAHEAD_HOURS = 24    # Predict matches in next 24h
```

## Monitoring

### Key Metrics

1. **Prediction Accuracy**
   - Outcome accuracy (win/draw/loss)
   - Score MAE (Mean Absolute Error)
   - Brier score (probabilistic accuracy)

2. **API Quota Usage**
   - Odds API calls remaining
   - Cache hit rate
   - API calls saved via caching

3. **System Health**
   - Recent predictions (24h)
   - Data freshness (hours since last update)
   - Cache entries (fresh vs stale)

### Alerting Thresholds

```python
ALERTS = {
    "odds_api_quota_low": 50,           # Remaining requests
    "cache_hit_rate_low": 0.5,          # 50%
    "data_stale_hours": 24,             # 24 hours
    "prediction_accuracy_low": 0.4,     # 40%
}
```

## Testing

### Unit Tests

```bash
# Run all World Cup tests
pytest app/tests/test_world_cup_*.py -v

# Test enhanced factors
python tests/manual/manual_enhanced_factors.py

# Test prediction engines
pytest app/tests/test_prediction_engines.py
```

### Integration Tests

```bash
# Test full pipeline
python -c "
from app.services.world_cup_prediction_service import predict_match
result = predict_match('test_match_id', engine='hybrid')
print(result)
"

# Test API endpoints
curl http://localhost:8000/api/analytics/system-health
```

## Troubleshooting

### Common Issues

**1. Missing predictions (confidence too low)**
- Check if betting odds are available
- Verify Elo ratings exist for both teams
- Ensure at least 3 core factors have data

**2. API quota exceeded (The Odds API)**
- Check cache hit rate: `GET /api/analytics/odds-cache-stats`
- Increase cache TTL for pre-match odds
- Reduce batch prediction frequency

**3. Stale data warnings**
- Verify scheduler is running: `GET /api/health`
- Check API-Football quota
- Manually trigger sync: `POST /api/world-cup/predictions/sync-fixtures`

**4. Slow predictions**
- Enable result caching (1 hour for stable predictions)
- Pre-compute predictions for upcoming matches
- Use `elo_odds` engine (faster than `hybrid`)

### Debug Mode

```python
# Enable verbose logging
import logging
logging.getLogger('app.services.world_cup_prediction_service').setLevel(logging.DEBUG)

# Inspect prediction factors
from app.services.world_cup_factor_service import calculate_team_factors
factors = calculate_team_factors('Brazil')
print(factors)
```

## License & Attribution

- **API-Football:** https://www.api-football.com/
- **The Odds API:** https://the-odds-api.com/
- **Transfermarkt:** https://www.transfermarkt.com/ (web scraping, respect rate limits)
- **Elo ratings:** Public domain algorithm

## Support

For issues or questions:
- Backend: `app/services/world_cup_*.py`
- Frontend: `frontend/src/components/world-cup/`
- Documentation: `WORLD_CUP_CONFIG.md` (this file)
