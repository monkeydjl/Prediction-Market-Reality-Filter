# Manual test scripts

These are **manual runner scripts**, not pytest tests. They were consolidated
here from the `backend/` root (see
`docs/reviews/pending-issues-2026-06-27.md` item P-14) to stop polluting the
root directory and to keep them out of CI's `pytest tests/` collection.

## Why they are not in the automated suite

- Most hit **real network** (Transfermarkt scrape, RSS feeds, The Odds API) or
  **real LLM** (`OPENAI_API_KEY` quota) and would burn credentials / quota on
  every CI run.
- Several write to the **production `world_cup_predictions.db`** and would
  pollute data.
- They use the `asyncio.run(main())` + `print()` runner pattern, not pytest
  test functions, so they are not collected by `pytest`'s default
  `test_*.py` pattern (files here are named `manual_*.py`).

Automated coverage for the same areas lives under `tests/`
(e.g. `tests/test_world_cup_elo_odds_engine.py`,
`tests/test_odds_cache_service.py`, `tests/test_world_cup_prediction_pipeline.py`).

## Running a script manually

From the `backend/` directory:

```bash
python tests/manual/manual_<name>.py
```

Each script prints its own progress. Set the required env vars (API keys,
`OPENAI_API_KEY`, etc.) in `backend/.env` first.

## Index

| Script | What it exercises | Requires |
|---|---|---|
| `manual_live_integration.py` | Full event-intelligence pipeline, single real LLM call | `OPENAI_API_KEY` |
| `manual_prediction_flow.py` | World Cup prediction flow on a mock fixture | `OPENAI_API_KEY`, writes to prediction DB |
| `manual_batch_prediction.py` | Batch prediction across multiple fixtures | `OPENAI_API_KEY`, writes to prediction DB |
| `manual_pipeline_integration.py` | Integrated pipeline with auto engine selection | `OPENAI_API_KEY`, writes to prediction DB |
| `manual_live_update.py` | Live match prediction updates | writes to prediction DB |
| `manual_engine_comparison.py` | Rule+AI vs Elo+Odds engine comparison | `OPENAI_API_KEY` |
| `manual_elo_odds_engine.py` | Elo+Odds fusion engine scenarios | none (pure math) |
| `manual_elo_odds_validation.py` | Elo+Odds validation across scenarios | none (pure math) |
| `manual_elo_ratings_service.py` | Elo ratings DB + FIFA-rank estimation | writes to prediction DB |
| `manual_enhanced_factors.py` | Enhanced factors (market value + sentiment) | network (Transfermarkt, RSS) |
| `manual_odds_api_real.py` | The Odds API with a real key | `ODDS_API_KEY` |
| `manual_odds_api_integration.py` | The Odds API integration | `ODDS_API_KEY` |
| `manual_odds_cache_service.py` | Odds caching service | writes to prediction DB |
| `manual_sentiment_aggregator.py` | News + Reddit sentiment aggregation | network (RSS, Reddit) |
| `manual_transfermarkt_scraper.py` | Transfermarkt market-value scraper | network (Transfermarkt) |
