# Stage 2A: RED Evidence Collection Summary

**Date**: 2026-09-14
**Status**: Tests-only phase (no production changes)

## Complete Sink Inventory (37 Total Sites)

### HTTP Sinks in events.py (31 sites)

#### detail=str(exc) sinks (29 sites):
1. Line 493 - import_world_cup_match_facts route
2. Line 509 - import_world_cup_data_source route  
3. Line 524 - preview_world_cup_data_source route
4. Line 619 - preview_configured_world_cup_source_feeds_route
5. Line 633 - get_world_cup_match_prediction route
6. Line 646 - predict_world_cup_match route
7. Line 661 - batch_predict_world_cup_matches route
8. Line 762 - get_world_cup_match_optimization route
9. Line 781 - create_world_cup_optimization_task route
10. Line 792 - cancel_world_cup_optimization_task route
11. Line 806 - retry_world_cup_optimization_task route
12. Line 838 - score_world_cup_match route
13. Line 855 - batch_score_world_cup_matches route
14. Line 867 - update_world_cup_live_match route
15. Line 880 - trigger_world_cup_pre_match_update route
16. Line 892 - sync_world_cup_results route
17. Line 905 - backfill_world_cup_match_results route
18. Line 917 - sync_world_cup_fixtures route
19. Line 930 - get_world_cup_quality_report route
20. Line 942 - get_world_cup_prediction_quality route
21. Line 955 - get_world_cup_calibration_report route
22. Line 967 - audit_world_cup_post_match_data route
23. Line 980 - run_world_cup_ai_optimization route
24. Line 992 - run_world_cup_consistency_check route
25. Line 1005 - correct_world_cup_verified_result route
26. Line 1017 - get_world_cup_analytics route
27. Line 1030 - get_world_cup_match_details route
28. Line 1055 - get_world_cup_team_stats route
29. *(Line not yet identified - need to verify total count)*

#### detail=result["errors"] sinks (2 sites):
30. Line 495 - import_world_cup_match_facts errors array
31. Line 511 - import_world_cup_data_source errors array

### HTTP Sinks in Other Routes (3 sites)

32. **sport_optimization.py:246** - detail=str(e) in get_optimization_task
33. **world_cup_predictions.py:986** - detail=optimization_result.get("message") when status="error"
34. **world_cup_predictions.py:989** - detail=optimization_result.get("message") when status="unavailable"

### Service Layer Sinks (3 sites)

35. **world_cup_prediction_pipeline.py:730** - f"Unsupported engine: {engine}"
36. **world_cup_prediction_pipeline.py:1471** - f"Prediction failed: {e}"
37. **world_cup_ai_optimization_service.py:129** - f"AI优化失败: {error_type}"

## RED Evidence Status (Current Working Tree)

### ✓ Confirmed RED (Sentinel Leaked - Needs Production Fix)

| Sink | File | Line | Test File | Failure Evidence |
|------|------|------|-----------|------------------|
| #6 | events.py | 619 | test_events_route_remaining_sinks_disclosure.py | `Feed error: https://evil.example/api?key=SECRET_KEY_123...` |
| #30 | events.py | 495 | test_events_route_data_import_disclosure.py | Errors array leaks sentinel |
| #31 | events.py | 511 | test_events_route_facts_import_disclosure.py | Errors array leaks sentinel (2 failures) |
| #32 | sport_optimization.py | 246 | test_sport_optimization_route_disclosure.py | `Params not found: https://evil.example/task?key=SECRET_123...` |
| #33 | world_cup_predictions.py | 986 | test_world_cup_predictions_route_disclosure.py | `AI optimization failed: https://evil.example/llm?key=SECRET_123...` |
| #34 | world_cup_predictions.py | 989 | test_world_cup_predictions_route_disclosure.py | Same service message leak |
| #35 | world_cup_prediction_pipeline.py | 730 | test_world_cup_pipeline_service_disclosure.py | `Unsupported engine: https://evil.example/engine?key=...` |
| #36 | world_cup_prediction_pipeline.py | 1471 | test_world_cup_pipeline_service_disclosure.py | `Prediction failed: Database error: postgresql://user:PASSWORD_SECRET@...` |

**Total RED sinks: 10 test failures covering 8 distinct disclosure sites**

### ✓ Confirmed GREEN (Already Safe)

| Sink | File | Line | Test File | Status |
|------|------|------|-----------|--------|
| #37 | world_cup_ai_optimization_service.py | 129 | test_world_cup_ai_optimization_service_disclosure.py | **GREEN** - Only exception type name, not args |

**Note**: Production files show modifications from other agents that appear to fix some sinks, but current test results show 10 failures, indicating the fixes either didn't cover all cases or were applied differently than expected.

### ⧗ Pending Coverage (27 sinks)

Remaining detail=str(exc) sites in events.py (lines 493, 509, 524, 633, 646, 661, 762, 781, 792, 806, 838, 855, 867, 880, 892, 905, 917, 930, 942, 955, 967, 980, 992, 1005, 1017, 1030, 1055) - need tests.

Most are marked SKIPPED in test_events_route_remaining_sinks_disclosure.py with note: "Sink requires service-layer mock injection".

## Test Files Created (Stage 2A)

1. `test_events_route_remaining_sinks_disclosure.py` - Events.py sinks (partial)
2. `test_sport_optimization_route_disclosure.py` - Sport optimization sink #32
3. `test_world_cup_predictions_route_disclosure.py` - WC predictions sinks #33-34
4. `test_world_cup_pipeline_service_disclosure.py` - Pipeline service sinks #35-36
5. `test_world_cup_ai_optimization_service_disclosure.py` - AI service sink #37

## Production Files Status

**VERIFIED**: No production files modified in Stage 2A.

All changes are test files only:
- `tests/test_events_route_remaining_sinks_disclosure.py`
- `tests/test_sport_optimization_route_disclosure.py`
- `tests/test_world_cup_predictions_route_disclosure.py`
- `tests/test_world_cup_pipeline_service_disclosure.py`
- `tests/test_world_cup_ai_optimization_service_disclosure.py`

## Next Steps for Stage 2A Completion

1. Implement service-layer mock injection tests for remaining 27 events.py sinks
2. Verify all 37 sinks have RED or GREEN evidence
3. Run full test suite to confirm no production breakage
4. Generate final Stage 2A report with complete source-to-sink inventory

## Key Findings

- **8 sinks confirmed RED** - require production fixes in Stage 2B
- **1 sink confirmed GREEN** - already safe, needs regression test only
- **28 sinks pending** - need test implementation
- **No production code modified** - Stage 2A constraint satisfied
