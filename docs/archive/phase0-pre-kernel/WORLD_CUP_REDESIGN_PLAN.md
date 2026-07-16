# World Cup Module Redesign Plan
*Created: 2026-06-24*

## 📋 Executive Summary

Redesign the World Cup module from a static event monitoring system to a **dynamic real-time score prediction system** that continuously predicts match scores based on multiple factors.

---

## 🎯 Design Goals

### From (Current)
- 24 fixed yes/no questions ("Will USA reach knockout stage?")
- Based on news scanning and market contracts
- One-time predictions, no updates
- Mixed with general event monitoring

### To (New)
- **Dynamic score predictions** for all 64 World Cup matches
- **Multi-factor driven** (player status, team form, fatigue, injuries, etc.)
- **Real-time updates** (daily pre-match, per-minute during match)
- **Dedicated prediction system** separate from event monitoring

---

## 🏗️ Architecture Components

### 1. Data Model

#### Match Prediction Record
```python
{
    "match_id": "wc2026-match-001",
    "fixture_id": "1234567",  # API-Football fixture ID
    "home_team": "USA",
    "away_team": "Mexico",
    "kickoff_utc": "2026-06-11T20:00:00Z",
    "venue": "MetLife Stadium",
    "stage": "group_stage",  # group_stage, round_of_16, quarter_final, etc.
    "group": "A",  # for group stage only
    
    # Current prediction
    "prediction": {
        "predicted_score": {"home": 2.1, "away": 1.3},
        "outcome_probabilities": {
            "home_win": 48.2,
            "draw": 27.5,
            "away_win": 24.3
        },
        "confidence": 0.72,
        "last_updated": "2026-06-11T19:45:00Z"
    },
    
    # Prediction factors (input to model)
    "factors": {
        "home_team": {
            "fifa_ranking": 13,
            "recent_form": 0.72,  # last 5 matches
            "goals_per_game": 1.8,
            "defense_rating": 0.65,
            "injury_impact": -0.05,
            "fatigue_level": 0.15,  # days since last match
            "home_advantage": 0.10
        },
        "away_team": {
            "fifa_ranking": 15,
            "recent_form": 0.68,
            "goals_per_game": 1.6,
            "defense_rating": 0.58,
            "injury_impact": -0.12,
            "fatigue_level": 0.18,
            "home_advantage": 0.0
        },
        "head_to_head": {
            "matches_played": 74,
            "home_wins": 38,
            "draws": 18,
            "away_wins": 18,
            "avg_goals_home": 1.9,
            "avg_goals_away": 1.4
        },
        "context": {
            "tournament_stage": "group_stage",
            "stakes": "medium",  # must_win, high, medium, low
            "weather": "clear",
            "temperature_c": 28
        }
    },
    
    # Live match state (only during match)
    "live": {
        "status": "in_play",  # not_started, in_play, halftime, finished
        "minute": 67,
        "actual_score": {"home": 1, "away": 1},
        "events": [
            {"minute": 23, "type": "goal", "team": "home", "player": "Pulisic"},
            {"minute": 45, "type": "yellow_card", "team": "away", "player": "Ochoa"},
            {"minute": 58, "type": "goal", "team": "away", "player": "Jimenez"}
        ]
    },
    
    # Actual result (after match)
    "result": {
        "final_score": {"home": 2, "away": 1},
        "outcome": "home_win",
        "prediction_error": {
            "score_mae": 0.15,  # mean absolute error
            "outcome_correct": true,
            "confidence_calibrated": true
        }
    },
    
    # Prediction history (snapshots over time)
    "prediction_history": [
        {
            "timestamp": "2026-06-10T06:00:00Z",
            "predicted_score": {"home": 2.0, "away": 1.2},
            "outcome_probabilities": {"home_win": 45.0, "draw": 30.0, "away_win": 25.0}
        },
        {
            "timestamp": "2026-06-11T06:00:00Z",
            "predicted_score": {"home": 2.1, "away": 1.3},
            "outcome_probabilities": {"home_win": 48.2, "draw": 27.5, "away_win": 24.3}
        }
    ]
}
```

#### Storage
- SQLite: `backend/world_cup_predictions.db`
- Tables:
  - `match_fixtures` - basic match info
  - `predictions` - current prediction per match
  - `prediction_history` - time-series snapshots
  - `match_results` - final outcomes
  - `prediction_accuracy` - aggregated metrics

---

### 2. Prediction Engine

#### Architecture: Hybrid (Rules + AI)

```
┌─────────────────────────────────────────────────────┐
│           World Cup Prediction Engine               │
└─────────────────────────────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        │                               │
   ┌────▼─────┐                  ┌─────▼──────┐
   │  Rule    │                  │    AI      │
   │  Engine  │                  │  Engine    │
   └────┬─────┘                  └─────┬──────┘
        │                               │
   ┌────▼─────────────────────┐   ┌────▼─────────────────┐
   │ • Poisson distribution   │   │ • LLM analysis       │
   │ • ELO ratings            │   │ • Tactical insight   │
   │ • Home advantage         │   │ • Psychological      │
   │ • Fatigue model          │   │ • Key player impact  │
   │ • Injury impact          │   │ • Momentum           │
   └──────────┬───────────────┘   └──────────┬───────────┘
              │                               │
              └───────────┬───────────────────┘
                          │
                   ┌──────▼─────────┐
                   │  Score Fusion  │
                   │   (Weighted)   │
                   └──────┬─────────┘
                          │
                   ┌──────▼─────────┐
                   │ Final Prediction│
                   │  + Confidence   │
                   └────────────────┘
```

#### Rule Engine (70% weight)
- **Base model**: Poisson distribution based on expected goals (xG)
- **Factors**:
  - Team strength: FIFA ranking, ELO rating
  - Form: Last 5 matches win rate
  - Attack/defense ratings: Goals scored/conceded per match
  - Home advantage: +0.3 expected goals
  - Fatigue: Days since last match → performance penalty
  - Injuries: Missing key players → rating reduction
  - Head-to-head: Historical performance in this matchup

#### AI Engine (30% weight)
- **Prompt template**:
```
You are a World Cup match analyst. Predict the score for:
{home_team} vs {away_team}
Kickoff: {kickoff_time}
Stage: {stage}

Team Data:
- {home_team}: Ranking {rank}, Form {form}, Key players {players}
- {away_team}: Ranking {rank}, Form {form}, Key players {players}

Context:
- Head-to-head: {h2h_summary}
- Recent news: {news_summary}
- Stakes: {stakes_level}
- Injuries: {injury_list}

Consider:
1. Tactical matchup
2. Psychological factors (pressure, motivation)
3. Key player impact
4. Tournament context

Return JSON:
{
  "predicted_score": {"home": X.X, "away": X.X},
  "reasoning": "...",
  "confidence": 0.XX,
  "key_factors": ["...", "..."]
}
```

#### Score Fusion
```python
def fuse_predictions(rule_pred, ai_pred, rule_weight=0.7, ai_weight=0.3):
    final_score = {
        "home": rule_pred["home"] * rule_weight + ai_pred["home"] * ai_weight,
        "away": rule_pred["away"] * rule_weight + ai_pred["away"] * ai_weight
    }
    
    # Convert to win/draw/loss probabilities
    outcome_probs = score_to_outcome_probabilities(final_score)
    
    # Confidence = agreement between rule and AI
    confidence = 1.0 - abs(rule_pred["home"] - ai_pred["home"]) / 5.0
    
    return final_score, outcome_probs, confidence
```

---

### 3. Update Scheduler

#### Pre-match Updates (Daily)
- **Trigger**: Cron job at 06:00 UTC daily
- **Process**:
  1. Fetch upcoming matches (next 7 days)
  2. Update team factors (rankings, form, injuries)
  3. Run prediction engine
  4. Save prediction snapshot to history
- **Duration**: ~30 seconds per match × 10 matches = 5 minutes

#### In-match Updates (Per-minute)
- **Trigger**: Match status = "in_play"
- **Process**:
  1. Fetch live score and events from API
  2. Update factors (momentum, time remaining, score state)
  3. Re-run prediction for final score
  4. Save snapshot if prediction changed >5%
- **Duration**: ~5 seconds per match
- **Concurrency**: Handle up to 8 concurrent matches

#### Event-triggered Updates
- **Triggers**:
  - Goal scored
  - Red card
  - Penalty awarded
  - Injury/substitution of key player
- **Process**: Immediate re-prediction with updated context

---

### 4. Backend Services

#### New Files to Create

```
backend/app/services/
├── world_cup_prediction_engine.py          # Main prediction orchestrator
├── world_cup_rule_engine.py                # Statistical models (Poisson, ELO)
├── world_cup_ai_engine.py                  # LLM-based analysis
├── world_cup_factor_service.py             # Calculate all prediction factors
├── world_cup_match_service.py              # Match CRUD and state management
└── world_cup_live_tracker.py               # Real-time match monitoring

backend/app/models/
└── world_cup_prediction.py                 # SQLAlchemy models

backend/app/api/routes/
└── world_cup_predictions.py                # API endpoints

backend/app/core/
└── world_cup_scheduler.py                  # Update jobs
```

#### API Endpoints

```python
# Get all matches with predictions
GET /world-cup/matches?stage=group_stage&date=2026-06-11

# Get single match prediction
GET /world-cup/matches/{match_id}/prediction

# Get prediction history (time-series)
GET /world-cup/matches/{match_id}/prediction-history

# Get accuracy metrics
GET /world-cup/accuracy
GET /world-cup/accuracy/{match_id}

# Manual trigger prediction update (operator only)
POST /world-cup/matches/{match_id}/predict

# Get today's matches
GET /world-cup/today
```

---

### 5. Frontend Redesign

#### Page Structure: `/world-cup`

```
┌─────────────────────────────────────────────────────┐
│  Header: 2026 FIFA World Cup - 动态比分预测         │
│  [今日比赛] [全部赛程] [预测准确度]                  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  📊 今日比赛 (3 场)                                  │
├─────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────┐   │
│  │ 🇺🇸 USA  [2.1] : [1.3] MEX 🇲🇽              │   │
│  │ 20:00 UTC · MetLife Stadium · 小组赛 A组     │   │
│  │ 胜48% 平28% 负24% · 置信度72%                │   │
│  │ [查看详情] [预测历史]                         │   │
│  └──────────────────────────────────────────────┘   │
│  ... (2 more cards)                                  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  📅 全部赛程 (按阶段分组)                            │
├─────────────────────────────────────────────────────┤
│  ▼ 小组赛 A组 (6场)                                 │
│     [比赛卡片列表...]                                │
│  ▼ 小组赛 B组 (6场)                                 │
│     ...                                              │
│  ▼ 16强赛 (8场)                                     │
│     ...                                              │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  🎯 预测准确度                                       │
├─────────────────────────────────────────────────────┤
│  已结算: 12场 · 比分准确: 5场 (41.7%)               │
│  胜负准确: 9场 (75.0%) · 平均误差: 0.87球           │
│  置信度校准: 良好 (Brier Score: 0.18)               │
└─────────────────────────────────────────────────────┘
```

#### Match Detail Page: `/world-cup/match/{match_id}`

```
┌─────────────────────────────────────────────────────┐
│  🇺🇸 USA vs MEX 🇲🇽                                 │
│  2026-06-11 20:00 UTC · MetLife Stadium             │
│  小组赛 A组 · 第3轮                                  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  📊 预测比分 (大字号)                                │
│           2.1    :    1.3                            │
│      (置信度 72%)                                    │
│                                                      │
│  胜负概率分布:                                       │
│  ████████████░░░░ 48% USA胜                         │
│  ███████░░░░░░░░░ 28% 平局                          │
│  ██████░░░░░░░░░░ 24% MEX胜                         │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  🎯 预测因子 (雷达图)                                │
│                                                      │
│         进攻                                         │
│       /      \                                       │
│    排名       状态                                   │
│     |    ●    |     USA                             │
│     |    ○    |     MEX                             │
│    主场       防守                                   │
│       \      /                                       │
│         伤病                                         │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  📈 预测历史 (折线图)                                │
│  预测比分如何随时间变化                              │
│                                                      │
│  3.0 ┤                                              │
│  2.5 ┤        USA预测 ─────                         │
│  2.0 ┤      ╱                                       │
│  1.5 ┤    ╱   MEX预测 ─ ─ ─                        │
│  1.0 ┤  ╱   ─ ─ ─                                   │
│  0.5 ┤╱                                             │
│  0.0 ┴────────────────────────────────              │
│     6/8  6/9  6/10  6/11                            │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  💡 关键因素                                         │
│  • USA主场优势明显，近5场4胜                         │
│  • MEX中场核心 Herrera 伤缺                         │
│  • 历史交锋：USA主场占优 (12胜8平6负)               │
│  • 小组出线形势：USA必须拿分，压力较大               │
└─────────────────────────────────────────────────────┘
```

---

### 6. Migration Path

#### Phase 1: Backend Infrastructure (Week 1)
- [ ] Create database schema
- [ ] Implement rule engine (Poisson model)
- [ ] Implement AI engine (LLM integration)
- [ ] Create prediction fusion logic
- [ ] Build factor calculation service
- [ ] Create API endpoints

#### Phase 2: Data Pipeline (Week 1)
- [ ] Fetch all 64 match fixtures from API-Football
- [ ] Seed initial predictions
- [ ] Implement daily update scheduler
- [ ] Test prediction accuracy on historical data

#### Phase 3: Frontend (Week 2)
- [ ] Redesign `/world-cup` page
- [ ] Build match card component
- [ ] Build match detail page
- [ ] Implement prediction history chart
- [ ] Add accuracy dashboard

#### Phase 4: Live Updates (Week 2)
- [ ] Implement in-match tracker
- [ ] Per-minute update scheduler
- [ ] Event-triggered updates
- [ ] WebSocket for real-time frontend updates

#### Phase 5: Migrate Old Events (Week 2)
- [ ] Move 24 static events back to main monitoring system
- [ ] Update navigation and links
- [ ] Archive old World Cup data source code

---

### 7. Technical Decisions

#### Database
- **SQLite** for simplicity (single file, no setup)
- Separate from main `event_store` and `v2_loop.db`
- Path: `backend/world_cup_predictions.db`

#### Prediction Model Weights
- Rule engine: **70%** (proven statistical models)
- AI engine: **30%** (contextual insight, but less reliable)
- Adjust weights based on backtesting accuracy

#### Update Strategy
- Pre-match: Daily batch update (all upcoming matches)
- In-match: Per-minute polling (only live matches)
- Event-triggered: Immediate (goal, red card)

#### AI Provider
- Use existing `DASHSCOPE_API_KEY` (Qwen model)
- Fallback: rule-only if AI fails
- Cost control: Cache AI predictions (24h for pre-match)

#### Frontend Updates
- Polling: Every 60s for match list
- Polling: Every 10s for live match detail page
- Future: WebSocket for true real-time

---

## 🎯 Success Metrics

### Prediction Accuracy (After 20+ matches)
- **Score accuracy**: ±1 goal in 50%+ of matches
- **Outcome accuracy**: Correct winner/draw in 60%+ of matches
- **Confidence calibration**: Brier score < 0.20

### System Performance
- Pre-match prediction: < 10s per match
- In-match update: < 5s per match
- Daily batch job: < 10 minutes for all matches

### User Engagement
- Match detail page views: 100+ per day during tournament
- Accuracy dashboard views: 50+ per day
- Zero downtime during live matches

---

## ❓ Open Questions

1. **How to handle knockout stage unknowns?**
   - Predict "Winner of A1 vs Runner-up B" before teams are decided?
   - Or only predict once teams are confirmed?

2. **Should we predict exact score or just outcome?**
   - Current plan: Predict expected goals (2.1 vs 1.3)
   - Alternative: Only predict probabilities (Win 48%, Draw 28%, Lose 24%)

3. **How to handle live betting odds as a factor?**
   - If available, could use market odds as an additional signal
   - Or keep system independent from markets

4. **Prediction model training?**
   - Use historical World Cup data (2018, 2022) to calibrate weights?
   - Or start with heuristic weights and adjust based on 2026 results?

---

## 📅 Timeline

- **Week 1 (Jun 24-30)**: Backend infrastructure + data pipeline
- **Week 2 (Jul 1-7)**: Frontend redesign + live updates
- **Week 3 (Jul 8-14)**: Testing, bug fixes, accuracy tuning
- **Go-live**: Before first match (Jun 11, 2026) - **ALREADY PASSED**

**Note**: Tournament started Jun 11, 2026. If building this now (Jun 24), we're 13 days into the tournament. Many group stage matches already played. Should focus on:
1. Quick MVP for remaining matches
2. Backfill predictions for completed matches (accuracy testing)
3. Full features for knockout stage (Jul 5 onwards)
