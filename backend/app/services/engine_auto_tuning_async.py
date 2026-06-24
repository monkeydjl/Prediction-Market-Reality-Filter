"""Async version of engine auto-tuning with background task support."""

import asyncio
from typing import Any
from datetime import datetime

from app.models.world_cup_prediction import (
    MatchFixture, MatchPrediction, AIOptimizedPrediction
)
from app.utils.prediction_db import get_prediction_session, close_prediction_session
from app.services.world_cup_ai_optimization_service import optimize_prediction_with_ai
from app.services.optimization_task_manager import get_task_manager
from app.services.engine_auto_tuning_service import (
    calculate_optimization_patterns,
    save_engine_calibration
)


async def run_async_optimization(engine_name: str, task_id: str) -> dict[str, Any]:
    """Run AI optimization in background with progress tracking.

    Args:
        engine_name: Engine to optimize ("elo_odds" or "hybrid")
        task_id: Task ID for progress tracking

    Returns:
        Optimization results
    """
    task_manager = get_task_manager()
    session = get_prediction_session()

    try:
        # Mark task as running
        await task_manager.mark_running(task_id)

        # Get all scheduled matches with predictions
        query = session.query(MatchFixture, MatchPrediction).join(
            MatchPrediction,
            MatchFixture.match_id == MatchPrediction.match_id
        ).filter(
            MatchFixture.status == "scheduled"
        )

        # Apply engine filter
        if engine_name:
            query = query.filter(
                MatchPrediction.prediction_method.like(f"%{engine_name}%")
            )

        matches = query.all()
        total_matches = len(matches)

        await task_manager.update_progress(
            task_id,
            progress=0,
            total=total_matches,
            log_message=f"开始优化 {total_matches} 场比赛"
        )

        results = {
            "total_processed": 0,
            "optimizations_generated": 0,
            "errors": []
        }

        # Process each match
        for idx, (fixture, prediction) in enumerate(matches):
            match_name = f"{fixture.home_team} vs {fixture.away_team}"

            await task_manager.update_progress(
                task_id,
                progress=idx,
                total=total_matches,
                current_match=match_name,
                log_message=f"[{idx+1}/{total_matches}] 正在分析: {match_name}"
            )

            try:
                # Run AI optimization with timeout
                optimization_result = await asyncio.wait_for(
                    optimize_prediction_with_ai(
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        current_prediction={
                            "predicted_score": {
                                "home": prediction.predicted_home_score,
                                "away": prediction.predicted_away_score
                            },
                            "outcome_probabilities": {
                                "home_win": prediction.home_win_prob,
                                "draw": prediction.draw_prob,
                                "away_win": prediction.away_win_prob
                            },
                            "confidence": prediction.confidence,
                            "elo_ratings": prediction.factors.get("elo_ratings") if prediction.factors else None
                        },
                        prediction_method=prediction.prediction_method,
                        match_context=None
                    ),
                    timeout=30.0  # 30 second timeout per match
                )

                results["total_processed"] += 1

                if optimization_result.get("status") == "ok":
                    opt = optimization_result.get("optimization", {})
                    opt_pred = opt.get("optimized_prediction")

                    if opt_pred:
                        # Save optimized prediction to database
                        optimized_record = AIOptimizedPrediction(
                            match_id=fixture.match_id,
                            original_engine=prediction.prediction_method,
                            original_home_score=prediction.predicted_home_score,
                            original_away_score=prediction.predicted_away_score,
                            original_home_win_prob=prediction.home_win_prob,
                            original_draw_prob=prediction.draw_prob,
                            original_away_win_prob=prediction.away_win_prob,
                            original_confidence=prediction.confidence,
                            optimized_home_score=opt_pred["predicted_score"]["home"],
                            optimized_away_score=opt_pred["predicted_score"]["away"],
                            optimized_home_win_prob=opt_pred["outcome_probabilities"]["home_win"],
                            optimized_draw_prob=opt_pred["outcome_probabilities"]["draw"],
                            optimized_away_win_prob=opt_pred["outcome_probabilities"]["away_win"],
                            optimized_confidence=opt_pred["confidence"],
                            blind_spots=opt.get("blind_spots", []),
                            calibration_issues=opt.get("calibration_issues", []),
                            optimization_reasoning=opt_pred.get("reasoning", "")
                        )
                        session.add(optimized_record)
                        session.commit()

                        results["optimizations_generated"] += 1

                        await task_manager.update_progress(
                            task_id,
                            progress=idx + 1,
                            total=total_matches,
                            log_message=f"✓ 优化完成: {match_name}"
                        )
                    else:
                        await task_manager.update_progress(
                            task_id,
                            progress=idx + 1,
                            total=total_matches,
                            log_message=f"⊘ 无优化建议: {match_name}"
                        )
                else:
                    error_msg = optimization_result.get("message", "Unknown error")
                    results["errors"].append({
                        "match_id": fixture.match_id,
                        "match": match_name,
                        "error": error_msg
                    })
                    await task_manager.update_progress(
                        task_id,
                        progress=idx + 1,
                        total=total_matches,
                        log_message=f"✗ 失败: {match_name} - {error_msg}"
                    )

            except asyncio.TimeoutError:
                error_msg = "AI优化超时(30秒)"
                results["errors"].append({
                    "match_id": fixture.match_id,
                    "match": match_name,
                    "error": error_msg
                })
                await task_manager.update_progress(
                    task_id,
                    progress=idx + 1,
                    total=total_matches,
                    log_message=f"✗ 超时: {match_name}"
                )

            except Exception as e:
                error_msg = str(e)
                results["errors"].append({
                    "match_id": fixture.match_id,
                    "match": match_name,
                    "error": error_msg
                })
                await task_manager.update_progress(
                    task_id,
                    progress=idx + 1,
                    total=total_matches,
                    log_message=f"✗ 错误: {match_name} - {error_msg}"
                )

        # Step 2: Calculate calibration from optimizations
        if results["optimizations_generated"] > 0:
            await task_manager.update_progress(
                task_id,
                progress=total_matches,
                total=total_matches,
                log_message="正在计算校准参数..."
            )

            pattern_analysis = calculate_optimization_patterns(engine_name)

            if pattern_analysis.get("status") == "ok":
                calibration_result = save_engine_calibration(
                    engine_name=engine_name,
                    calibration_params=pattern_analysis["calibration_params"],
                    based_on_matches=pattern_analysis["samples"],
                    description=f"Auto-tuned from {pattern_analysis['samples']} AI optimizations"
                )

                final_result = {
                    "status": "ok",
                    "engine": engine_name,
                    "optimization_summary": results,
                    "pattern_analysis": pattern_analysis["analysis"],
                    "calibration": calibration_result,
                    "top_blind_spots": pattern_analysis["top_blind_spots"],
                    "top_calibration_issues": pattern_analysis["top_calibration_issues"]
                }
            else:
                final_result = {
                    "status": "partial",
                    "engine": engine_name,
                    "optimization_summary": results,
                    "message": "优化完成但无法生成校准参数"
                }
        else:
            final_result = {
                "status": "no_data",
                "engine": engine_name,
                "optimization_summary": results,
                "message": f"未生成任何优化建议（{results['total_processed']} 场比赛已处理）"
            }

        # Mark task as completed
        await task_manager.mark_completed(task_id, final_result)
        return final_result

    except Exception as e:
        # Mark task as failed
        error_msg = f"优化任务失败: {str(e)}"
        await task_manager.mark_failed(task_id, error_msg)
        return {
            "status": "error",
            "message": error_msg
        }

    finally:
        close_prediction_session(session)
