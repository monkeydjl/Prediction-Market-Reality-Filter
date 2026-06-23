# The Odds API Configuration Guide

## Getting Started

1. **Get Free API Key**
   - Visit: https://the-odds-api.com/
   - Sign up for free account
   - Get API key (500 requests/month free tier)

2. **Configure API Key**
   ```bash
   # Add to backend/.env
   ODDS_API_KEY=your_api_key_here
   ODDS_API_ENABLED=true
   ```

3. **Test Configuration**
   ```bash
   cd backend
   python test_odds_api_real.py
   ```

## API Quota Management

### Free Tier: 500 requests/month

**World Cup 2026 Estimated Usage: ~354 requests (71% utilization)**

### Breakdown by Phase

#### Pre-Tournament (100 requests)
- Initial fixture scraping: 48 matches × 1 request = 48
- Pre-tournament updates (1 week before): 48 matches × 1 refresh = 48
- Buffer for retries: 4 requests
- **Subtotal: 100 requests**

#### Group Stage (150 requests)
- 48 matches in group stage
- Daily updates (7 days): 48 matches × 2 updates = 96 requests
- Match-day refreshes (closer to kickoff): 48 matches × 1 refresh = 48
- Buffer: 6 requests
- **Subtotal: 150 requests**

#### Knockout Stage (104 requests)
- Round of 16: 8 matches × 3 updates = 24 requests
- Quarter-finals: 4 matches × 4 updates = 16 requests
- Semi-finals: 2 matches × 6 updates = 12 requests
- Third place: 1 match × 6 updates = 6 requests
- Final: 1 match × 8 updates = 8 requests
- Pre-match updates (all knockout): 16 matches × 2 = 32 requests
- Buffer: 6 requests
- **Subtotal: 104 requests**

### Caching Strategy

**TTL by Phase:**
- Pre-tournament (>7 days): 24 hours
- Tournament week (2-7 days): 12 hours
- Match day (0-2 days): 1 hour
- Live (during match): 5 minutes

**Implementation:**
```python
from app.services.odds_cache_service import get_cached_odds

# Automatic TTL based on commence time
odds = await get_cached_odds(
    home_team="Brazil",
    away_team="Argentina",
    ttl_seconds=3600,  # 1 hour
    commence_time="2026-06-24T18:00:00Z"
)
```

## API Endpoints Used

### 1. Fetch Match Odds
```
GET /v4/sports/soccer_fifa_world_cup/odds
```

**Parameters:**
- `apiKey`: Your API key
- `regions`: us,eu (US and European bookmakers)
- `markets`: h2h (1x2 odds: home/draw/away)
- `oddsFormat`: decimal
- `dateFormat`: iso

**Response:**
```json
{
  "id": "abc123",
  "sport_key": "soccer_fifa_world_cup",
  "commence_time": "2026-06-24T18:00:00Z",
  "home_team": "Brazil",
  "away_team": "Argentina",
  "bookmakers": [
    {
      "key": "pinnacle",
      "title": "Pinnacle",
      "markets": [
        {
          "key": "h2h",
          "outcomes": [
            {"name": "Brazil", "price": 2.10},
            {"name": "Draw", "price": 3.20},
            {"name": "Argentina", "price": 3.50}
          ]
        }
      ]
    }
  ]
}
```

## Monitoring Quota

### Check Response Headers
Every API response includes quota information:
```
x-requests-used: 245
x-requests-remaining: 255
```

### Alert Thresholds
- **< 100 remaining**: Warning - reduce update frequency
- **< 50 remaining**: Critical - pause non-essential updates
- **< 20 remaining**: Emergency - only pre-match updates

### Quota Reset
- Resets monthly on your signup anniversary date
- Track usage in logs: `backend/logs/odds_api_usage.log`

## Best Practices

### 1. Use Cache Aggressively
```python
# Good: Use cache with appropriate TTL
odds = await get_cached_odds(home_team, away_team, ttl_seconds=3600)

# Bad: Always fetch fresh (wastes quota)
odds = await fetch_match_odds(home_team, away_team)
```

### 2. Batch Prefetch
```python
# Prefetch all day's matches at once
matches = get_today_matches()
await prefetch_matches_odds(matches, ttl_seconds=3600)
```

### 3. Prioritize Important Matches
- Knockout stage > Group stage
- Closer to kickoff > Far future
- High-profile matches > Lower-profile

### 4. Graceful Degradation
System works without odds:
- Falls back to Elo-only predictions
- Displays "no odds available" in UI
- Uses hybrid engine instead of Elo+Odds

## Troubleshooting

### No Odds Available
**Possible causes:**
1. World Cup 2026 odds not yet posted (bookmakers typically post 1-3 months before)
2. Invalid API key
3. Exceeded quota
4. Wrong sport key

**Solution:**
Check API response and logs:
```bash
tail -f backend/logs/odds_api.log
```

### High Quota Usage
**Symptoms:**
- Approaching 500 requests before month end
- Frequent "quota exceeded" errors

**Solutions:**
1. Increase cache TTL
2. Reduce update frequency
3. Disable odds for low-priority matches
4. Upgrade to paid tier (2000 requests/month for $25)

## Cost Analysis

### Free Tier (500 requests/month)
- **Cost**: $0
- **Coverage**: Full World Cup with smart caching
- **Limitation**: Requires careful quota management

### Paid Tier (2000 requests/month)
- **Cost**: $25/month
- **Coverage**: Multiple tournaments or more frequent updates
- **Benefit**: More flexibility, less cache dependency

## Production Checklist

- [ ] API key configured in `.env`
- [ ] `ODDS_API_ENABLED=true`
- [ ] Cache database initialized
- [ ] Quota monitoring set up
- [ ] Alert thresholds configured
- [ ] Backup prediction method (Elo-only) tested
- [ ] Batch prefetch scheduled
- [ ] Logs configured and monitored

## Support

- **Documentation**: https://the-odds-api.com/liveapi/guides/v4/
- **Support**: contact@the-odds-api.com
- **Status Page**: https://status.the-odds-api.com/
