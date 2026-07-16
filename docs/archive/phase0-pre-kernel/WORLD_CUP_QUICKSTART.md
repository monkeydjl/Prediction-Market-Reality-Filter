# World Cup Prediction Module - Quick Start Guide

## 1. Installation (5 minutes)

### Backend Setup

```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env

# Edit .env and add required API keys:
# - OPENAI_API_KEY (required for AI predictions)
# - API_FOOTBALL_KEY (required for fixtures)
# - ODDS_API_KEY (required for betting odds)
```

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

## 2. Database Initialization (1 minute)

```bash
# Initialize prediction database tables
curl -X POST http://localhost:8000/api/world-cup/predictions/init-db
```

## 3. Sync Initial Data (2 minutes)

```bash
# Sync World Cup fixtures from API-Football
curl -X POST http://localhost:8000/api/world-cup/predictions/sync-fixtures

# Verify fixtures were loaded
curl http://localhost:8000/api/world-cup/predictions/matches | jq '.matches | length'
```

## 4. Generate First Prediction (30 seconds)

```bash
# Get a match ID
MATCH_ID=$(curl -s http://localhost:8000/api/world-cup/predictions/matches | jq -r '.matches[0].match_id')

# Trigger prediction (auto engine selection)
curl -X POST "http://localhost:8000/api/world-cup/predictions/matches/$MATCH_ID/predict" \
  -H "Content-Type: application/json" \
  -d '{"engine": "auto"}'

# View prediction
curl "http://localhost:8000/api/world-cup/predictions/matches/$MATCH_ID" | jq
```

## 5. Access Frontend (immediate)

Open browser: http://localhost:3000/world-cup

**Features:**
- View all fixtures by stage
- Trigger predictions with one click
- Compare Elo+Odds vs Hybrid engine
- View prediction history timeline
- Monitor system analytics

## 6. Verify Enhanced Data (optional)

### Test Market Value Integration

```bash
python tests/manual/manual_enhanced_factors.py
```

Expected output:
```
Testing enhanced factor integration...

Brazil market value: €928.2m
Market value rating: 0.916 (0.0 = €150m, 1.0 = €1000m+)

✓ Market value integration working
✓ Sentiment integration working
✓ Enhanced factors test passed
```

### Check Cache Statistics

```bash
curl http://localhost:8000/api/analytics/odds-cache-stats | jq
```

## 7. Batch Predictions (optional)

```bash
# Predict all upcoming matches in next 24 hours
curl -X POST http://localhost:8000/api/world-cup/predictions/batch-predict

# Check system health
curl http://localhost:8000/api/analytics/system-health | jq
```

## Architecture Overview

```
Frontend (React + Next.js)
    ↓ HTTP requests
Backend API (FastAPI)
    ↓
┌──────────────────────────────────────┐
│   Prediction Service                 │
│                                      │
│   ┌─────────────┐   ┌─────────────┐│
│   │ Elo+Odds    │   │   Hybrid    ││
│   │  Engine     │   │   Engine    ││
│   └─────────────┘   └─────────────┘│
│           ↓                ↓        │
│   ┌──────────────────────────────┐ │
│   │  Enhanced Data Layer         │ │
│   │  - Market Value (Transfermkt)│ │
│   │  - Sentiment (News+Reddit)   │ │
│   │  - Betting Odds (cached)     │ │
│   └──────────────────────────────┘ │
└──────────────────────────────────────┘
    ↓
SQLite Database (prediction.db)
```

## API Endpoints Reference

### Fixtures

```bash
# Get all matches
GET /api/world-cup/predictions/matches

# Filter by stage
GET /api/world-cup/predictions/matches?stage=GROUP_STAGE

# Filter by status
GET /api/world-cup/predictions/matches?status=scheduled

# Get today's matches
GET /api/world-cup/predictions/today
```

### Predictions

```bash
# Get match with prediction
GET /api/world-cup/predictions/matches/{match_id}

# Trigger prediction
POST /api/world-cup/predictions/matches/{match_id}/predict
Body: {"engine": "elo_odds" | "hybrid" | "auto"}

# Batch predict
POST /api/world-cup/predictions/batch-predict

# Prediction history
GET /api/world-cup/predictions/matches/{match_id}/prediction-history
```

### Analytics

```bash
# Engine statistics
GET /api/analytics/engine-stats

# Accuracy metrics
GET /api/analytics/accuracy-stats

# Cache performance
GET /api/analytics/odds-cache-stats

# System health
GET /api/analytics/system-health

# Match timeline
GET /api/analytics/prediction-timeline?match_id={id}
```

## Prediction Engines

### Elo + Odds Engine (Fast, No LLM)

**When to use:** Quick predictions, low API cost, pre-match odds available

**Factors:**
- Elo rating (35%)
- Recent form (25%)
- Betting odds (40%)

**Response time:** ~200ms

```bash
curl -X POST "http://localhost:8000/api/world-cup/predictions/matches/$MATCH_ID/predict" \
  -H "Content-Type: application/json" \
  -d '{"engine": "elo_odds"}'
```

### Hybrid Engine (Comprehensive, Uses LLM)

**When to use:** Important matches, all data available, accuracy priority

**Factors:**
- Elo rating (25%)
- Recent form (20%)
- Betting odds (25%)
- Market value (15%)
- Sentiment (10%)
- Head-to-head (5%)

**Response time:** ~2-5s (LLM call)

```bash
curl -X POST "http://localhost:8000/api/world-cup/predictions/matches/$MATCH_ID/predict" \
  -H "Content-Type: application/json" \
  -d '{"engine": "hybrid"}'
```

### Auto Engine (Adaptive)

**Logic:**
- If betting odds available → Elo+Odds (fast, market-informed)
- If no odds → Hybrid (uses all available factors)

```bash
curl -X POST "http://localhost:8000/api/world-cup/predictions/matches/$MATCH_ID/predict" \
  -H "Content-Type: application/json" \
  -d '{"engine": "auto"}'
```

## Monitoring & Debugging

### Check if predictions are working

```bash
# Should show predictions
curl http://localhost:8000/api/analytics/engine-stats | jq '.total_predictions'

# Should be "healthy"
curl http://localhost:8000/api/analytics/system-health | jq '.status'
```

### View logs

```bash
# Backend logs (FastAPI)
tail -f logs/app.log

# Filter for prediction events
tail -f logs/app.log | grep world_cup
```

### Common Issues

**No predictions generated (confidence too low)**
- Check if betting odds are cached: `GET /api/analytics/odds-cache-stats`
- Verify Elo ratings exist for teams
- Try `hybrid` engine explicitly

**Odds API quota exceeded**
- Check remaining: Response header `x-requests-remaining`
- Increase cache TTL in .env
- Use cached predictions (don't re-trigger)

**Fixtures not syncing**
- Verify API_FOOTBALL_KEY in .env
- Check API quota: https://dashboard.api-football.com/
- Manual sync: `POST /api/world-cup/predictions/sync-fixtures`

## Production Deployment

### Environment Variables

```bash
# Required
OPENAI_API_KEY=sk-...
API_FOOTBALL_KEY=...
ODDS_API_KEY=...

# Security
API_WRITE_KEY=secure-random-key
ALLOW_OPEN_WRITES=false
ENVIRONMENT=production

# Database (use PostgreSQL)
DATABASE_URL=postgresql://user:pass@host:5432/db

# CORS (set to your domain)
CORS_ALLOWED_ORIGINS=https://your-domain.com
```

### Database Migration

```bash
# Export from SQLite
sqlite3 prediction.db .dump > backup.sql

# Import to PostgreSQL
psql $DATABASE_URL < backup.sql

# Or use a migration tool
alembic upgrade head
```

### Performance Tuning

```python
# Increase cache TTLs to reduce API calls
ODDS_CACHE_TTL_PREMATCH=7200  # 2 hours

# Pre-compute predictions for upcoming matches
# (schedule batch-predict every 6 hours)
*/6 * * * * curl -X POST http://localhost:8000/api/world-cup/predictions/batch-predict
```

### Monitoring Setup

```bash
# Health check endpoint
curl http://localhost:8000/api/analytics/system-health

# Alert if status != "healthy"
# Alert if recent_predictions_24h == 0
# Alert if data_freshness_hours > 24
```

## Testing

### Run Backend Tests

```bash
cd backend

# All World Cup tests
pytest app/tests/test_world_cup_*.py -v

# Specific test
python tests/manual/manual_enhanced_factors.py
```

### Frontend Component Tests

```bash
cd frontend

# Run tests
npm test

# Specific component
npm test -- match-prediction-card
```

## Next Steps

1. **Enable Enhanced Data** (optional)
   - Add REDDIT_CLIENT_ID, NEWS_API_KEY to .env
   - Market values and sentiment will auto-populate

2. **Set Up Automated Sync**
   - Enable scheduler: `SCHEDULER_ENABLED=true`
   - Fixtures sync daily at 5:20 UTC
   - Batch predictions every 6 hours

3. **Add Result Tracking**
   - Implement match result webhook
   - Auto-calculate accuracy metrics
   - View on analytics dashboard

4. **Customize Weights**
   - Edit `app/services/world_cup_factor_service.py`
   - Adjust factor weights based on accuracy analysis
   - A/B test different weight configurations

## Support & Documentation

- **Full Config Guide:** `WORLD_CUP_CONFIG.md`
- **API Documentation:** http://localhost:8000/docs
- **Frontend Storybook:** `npm run storybook`

## License

MIT License - See LICENSE file for details
