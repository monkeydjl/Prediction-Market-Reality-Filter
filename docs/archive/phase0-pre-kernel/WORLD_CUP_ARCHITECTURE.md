# World Cup Static Events - Architecture Documentation

## Overview

The World Cup module supports TWO types of predictions:

1. **Dynamic Score Predictions** (NEW)
   - Real-time match score predictions
   - Updated pre-match, during match, and post-match
   - Displayed on `/world-cup` page
   - Uses hybrid AI model (70% rules + 30% AI)

2. **Static Event Questions** (LEGACY)
   - Yes/no questions about tournament outcomes
   - Examples: "Will Brazil reach semifinals?", "Will there be 8+ red cards?"
   - Tracked in main event monitoring system
   - Accessible via `/events?source=2026+FIFA+World+Cup`

## Static Events Integration

### Event Source
File: `backend/app/services/world_cup_event_source.py`

Defines 24 curated World Cup questions across categories:
- **team_progression**: Knockout stage, semifinals, finals (12 events)
- **group_stage**: Group winners, advancement (5 events)
- **match_format**: Penalty shootouts, extra time (2 events)
- **player_awards**: Top scorer predictions (1 event)
- **discipline**: Red card counts (1 event)
- **tournament_totals**: Total goals (1 event)
- **tournament_winner**: Champion prediction (2 events)

### Discovery Process
The events enter the tracking system automatically through:

1. **Event Discovery Scheduler** (`scheduler.py`)
   - Runs daily at 07:15 UTC
   - Calls `_job_event_discover()`

2. **Event Discovery Service** (`event_discovery_service.py`)
   - Fetches candidates from all sources including World Cup
   - Calls `fetch_candidate_events()` from `world_cup_event_source.py`

3. **Configuration**
   ```python
   WORLD_CUP_SOURCE_ENABLED = true  # Default: enabled
   WORLD_CUP_SOURCE_NAME = "2026 FIFA World Cup"
   ```

### Frontend Access

**World Cup Page** (`/world-cup`)
- Shows dynamic score predictions
- Has link at bottom: "查看传统世界杯事件（小组出线、金靴奖等）"
- Links to: `/events?source=2026+FIFA+World+Cup`

**Events Page** (`/events`)
- Standard event monitoring interface
- Filter by source: "2026 FIFA World Cup"
- Shows all 24 static questions with probabilities

## Why Two Systems?

**Dynamic Predictions** are fundamentally different from **Static Events**:

| Aspect | Dynamic Predictions | Static Events |
|--------|---------------------|---------------|
| **Nature** | Continuous numerical scores | Binary yes/no questions |
| **Update Frequency** | Every 2 minutes (live) | Daily probability updates |
| **Data Model** | MatchFixture + MatchPrediction | TrackedEntry (event store) |
| **Resolution** | Match ends → final score | Tournament ends → yes/no |
| **Complexity** | 64 matches × 2 teams | 24 fixed questions |
| **User Value** | Betting insights | Tournament storylines |

Combining them would create confusion and reduce usability.

## Current Status

✅ **Complete Integration**
- Static events defined in `world_cup_event_source.py`
- Event discovery configured and enabled
- Frontend link properly routes users
- Both systems coexist cleanly

📋 **No Migration Needed**
The "migration" task was actually about ensuring the static events remain accessible
through the standard event monitoring system while the new prediction page focuses
on dynamic scores. This architecture is already correct.

## Testing

To verify static events are being discovered:

```bash
cd backend

# Check event source
python -c "from app.services.world_cup_event_source import fetch_candidate_events; import asyncio; events = asyncio.run(fetch_candidate_events(limit=30)); print(f'Events available: {len(events)}')"

# Check tracked events (after discovery has run)
python check_world_cup_events.py
```

If no events are tracked yet, run event discovery manually:
```python
from app.services.event_discovery_service import run_event_discover
import asyncio
result = asyncio.run(run_event_discover())
print(result)
```

## Future Enhancements

1. **Cross-reference predictions**: Link dynamic match predictions to static knockout questions
2. **Auto-resolution**: Use match results to automatically resolve static events
3. **Unified dashboard**: Show both prediction types in a comprehensive view
4. **Historical tracking**: Compare predicted vs actual outcomes across both systems
