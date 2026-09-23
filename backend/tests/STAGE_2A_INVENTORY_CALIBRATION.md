# Stage 2A Inventory Calibration (2026-09-20)

Read-only recount from the **current** source on branch `fix/scheduler-ledger-alarm-gate`
(HEAD `484ab77`). Method: `ast.parse` of `app/api/routes/events.py`, enumerate every
`raise HTTPException(...)`, classify the `detail=` argument. This **does not overwrite**
the earlier `STAGE_2A_*.md`; it supersedes their counts.

## Count: before vs after

| Quantity | Old report claimed | Calibrated (AST) | Note |
|---|---|---|---|
| events.py `detail=str(exc)` | 29 | **28** | no `str(e)` variant exists |
| events.py `detail=result["errors"]` | 2 | **2** | lines 495, 511 |
| events.py total disclosure sinks | 31 | **30** | 28 + 2 |
| sport_optimization.py `detail=str(e)` | 1 | **1** | line 246 |
| sport_optimization.py `f"...{sport}"` reflection | (not counted) | **3** (R1–R3) | lines 83/87/116 |
| world_cup_predictions.py `optimization_result.message` | 2 | **2** | lines 986/989 |
| **HTTP grand total** | 34 | **36** | 30 + 1 + 3 + 2 |

### Where the phantom "29th str(exc)" came from
`events.py:703` in `validate_api_football_pipeline_route`
(`POST /sports/world-cup/data/bundle/api-football/validate`):

```python
error = f"API-Football pipeline validation failed: {type(exc).__name__}"
raise HTTPException(status_code=500, detail=error) from exc
```

The `detail=` value is the **variable `error`**, whose text is a fixed message plus
`type(exc).__name__` — NOT `str(exc)`. It is already safe (GREEN). The old inventory
appears to have folded this row into its `str(exc)` count, producing 29. There is **no
29th `str(exc)` sink**; the correct count is 28.

Full AST classification of the 62 `raise HTTPException` in events.py:
- 28 `str(exc)`
- 11 `f-string`
- 2 `result['errors']`
- 1 `name:error` (line 703, safe — the phantom "29th")
- 20 fixed string literals (Event not found, Invalid source configuration, etc.)

## events.py 30-sink map (line | method | route | function | type | test | status)

| # | Line | Route | Function | Type | Test file / name | Status |
|---|---|---|---|---|---|---|
| 1 | 493 | POST /sports/world-cup/facts/import | import_world_cup_facts | str(exc) | remaining_sinks[sink2_line493] | RED |
| — | 495 | POST /sports/world-cup/facts/import | import_world_cup_facts | result["errors"] | facts_import_disclosure | RED |
| 2 | 509 | POST /sports/world-cup/data/import | import_world_cup_data_source | str(exc) | remaining_sinks[sink4_line509] | RED |
| — | 511 | POST /sports/world-cup/data/import | import_world_cup_data_source | result["errors"] | data_import_disclosure | RED |
| 3 | 524 | POST /sports/world-cup/data/preview | preview_world_cup_data_source | str(exc) | remaining_sinks[sink5_line524] | RED |
| 4 | 619 | POST /sports/world-cup/data/bundle/feeds/preview | preview_configured_world_cup_source_feeds_route | str(exc) | remaining_sinks[sink6_line619] | RED |
| 5 | 633 | POST /sports/world-cup/data/bundle/feeds/import | import_configured_world_cup_source_feeds_route | str(exc) | remaining_sinks[sink7_line633] | RED |
| 6 | 646 | POST /sports/world-cup/data/bundle/api-football/preview | preview_api_football_world_cup_source_bundle_route | str(exc) | remaining_sinks[sink8_line646] | RED |
| 7 | 661 | POST /sports/world-cup/data/bundle/api-football/import | import_api_football_world_cup_source_bundle_route | str(exc) | remaining_sinks[sink9_line661] | RED |
| 8 | 762 | POST /sports/world-cup/data/bundle/football-data/preview | preview_football_data_world_cup_standings_route | str(exc) | remaining_sinks[sink10_line762] | RED |
| 9 | 781 | POST /sports/world-cup/data/bundle/football-data/import | import_football_data_world_cup_standings_route | str(exc) | remaining_sinks[sink11_line781] | RED |
| 10 | 792 | POST /sports/world-cup/data/bundle/sportmonks/preview | preview_sportmonks_world_cup_source_bundle_route | str(exc) | remaining_sinks[sink12_line792] | RED |
| 11 | 806 | POST /sports/world-cup/data/bundle/sportmonks/import | import_sportmonks_world_cup_source_bundle_route | str(exc) | remaining_sinks[sink13_line806] | RED |
| 12 | 838 | POST /sports/world-cup/data/source/preview | preview_configured_world_cup_data_source | str(exc) | remaining_sinks[sink14_line838] | RED |
| 13 | 855 | POST /sports/world-cup/data/source/import | import_configured_world_cup_data_source | str(exc) | remaining_sinks[sink15_line855] | RED |
| 14 | 867 | POST /sports/world-cup/official-csv/preview | preview_world_cup_official_csv_source_route | str(exc) | remaining_sinks[sink16_line867] | RED |
| 15 | 880 | POST /sports/world-cup/official-csv/import | import_world_cup_official_csv_source_route | str(exc) | remaining_sinks[sink17_line880] | RED |
| 16 | 892 | POST /sports/world-cup/matches/preview | preview_world_cup_match_source_route | str(exc) | remaining_sinks[sink18_line892] | RED |
| 17 | 905 | POST /sports/world-cup/matches/import | import_world_cup_match_source_route | str(exc) | remaining_sinks[sink19_line905] | RED |
| 18 | 917 | POST /sports/world-cup/match-events/preview | preview_world_cup_match_events_source_route | str(exc) | remaining_sinks[sink20_line917] | RED |
| 19 | 930 | POST /sports/world-cup/match-events/import | import_world_cup_match_events_source_route | str(exc) | remaining_sinks[sink21_line930] | RED |
| 20 | 942 | POST /sports/world-cup/lineups/preview | preview_world_cup_lineups_source_route | str(exc) | remaining_sinks[sink22_line942] | RED |
| 21 | 955 | POST /sports/world-cup/lineups/import | import_world_cup_lineups_source_route | str(exc) | remaining_sinks[sink23_line955] | RED |
| 22 | 967 | POST /sports/world-cup/standings/preview | preview_world_cup_standings_source_route | str(exc) | remaining_sinks[sink25_line967] | RED |
| 23 | 980 | POST /sports/world-cup/standings/import | import_world_cup_standings_source_route | str(exc) | remaining_sinks[sink26_line980] | RED |
| 24 | 992 | POST /sports/world-cup/player-awards/preview | preview_world_cup_player_awards_source_route | str(exc) | remaining_sinks[sink27_line992] | RED |
| 25 | 1005 | POST /sports/world-cup/player-awards/import | import_world_cup_player_awards_source_route | str(exc) | remaining_sinks[sink28_line1005] | RED |
| 26 | 1017 | POST /sports/world-cup/player-status/preview | preview_world_cup_player_status_source_route | str(exc) | remaining_sinks[sink29_line1017] | RED |
| 27 | 1030 | POST /sports/world-cup/player-status/import | import_world_cup_player_status_source_route | str(exc) | remaining_sinks[sink30_line1030] | RED |
| 28 | 1055 | POST /sports/world-cup/statistics/import | import_world_cup_statistics_source_route | str(exc) | remaining_sinks[sink31_line1055] | RED |

Note: the `remaining_sinks` parametrize ids skip `sink1/sink3/sink24` because those ids
are reserved for the `result["errors"]` rows (495/511) and the preview/statistics rows
covered by their own companions; the 28 `str(exc)` rows are all present.

Already-safe in events.py (NOT sinks): 703 (`type(exc).__name__`), and the 20 literal details.

## Additional (non-events) sinks

| ID | File | Line | Route / entry | Type | Test | Status |
|---|---|---|---|---|---|---|
| S1 | sport_optimization.py | 246 | POST /api/sport-optimization/apply/{id} | str(e) | sport_opt_disclosure::test_apply_params_valueerror | RED |
| R1 | sport_optimization.py | 83 | POST /api/sport-optimization/backfill-seed | f"Unsupported sport: {sport}" | sport_opt[line83_backfill] | RED |
| R2 | sport_optimization.py | 87 | POST /api/sport-optimization/backfill-seed | f"...unsupported sport: {sport}" | sport_opt[line87_seed_elo] | RED |
| R3 | sport_optimization.py | 116 | POST /api/sport-optimization/run | f"Unsupported sport: {sport}" | sport_opt[line116_run] | RED |
| P33 | world_cup_predictions.py | 986 | POST .../matches/{id}/optimize (error) | optimization_result.message | wc_pred_disclosure[sink33] | RED |
| P34 | world_cup_predictions.py | 989 | POST .../matches/{id}/optimize (unavailable) | optimization_result.message | wc_pred_disclosure[sink34] | RED |

## Service-layer / defence-in-depth / quality-gate items

| ID | File | Line | Kind | Status | Required action |
|---|---|---|---|---|---|
| B1 | world_cup_prediction_pipeline.py | 1471 | `f"Prediction failed: {e}"` in public result dict | RED (service result unsafe) | fix to fixed/classified error |
| A1 | world_cup_prediction_pipeline.py | 730 | `f"Unsupported engine: {engine}"` | HTTP boundary GREEN (422 via Literal); direct-call echo RED | defence-in-depth or document residual |
| C1 | world_cup_ai_optimization_service.py | 129 | `f"AI优化失败: {error_type}"`, error_type=type(exc).__name__ | GREEN (type name only) | add behavioural regression test |
| Q1 | app/core/logging.py | 61 | mypy union-attr | OPEN | fix (no bare ignore/cast) |
| Q2 | app/utils/sentry.py | 110 | mypy before_send arg-type | OPEN | fix (correct SDK type) |

## Corrected grand totals
- HTTP sinks = **36** (events 30 + sport 1 + reflection 3 + wc_pred 2)
- Service/defence/quality = B1 (fix), A1 (defence/doc), C1 (regression), Q1/Q2 (mypy)
- NOT the "34" the brief inherited: the 34 assumed 31 events sinks (29 str(exc) + 2 errors);
  the real events count is 30 (28 + 2), and the 3 `sport` reflection sinks were never in the 34.

Status: NO-SHIP. Calibration only; no production files modified.
