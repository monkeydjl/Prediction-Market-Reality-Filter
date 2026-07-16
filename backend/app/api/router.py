# app/api/router.py — v0.3.0
from fastapi import APIRouter
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions, sport_markets, sport_edges, sport_recommendations, sport_settlements, sport_odds

api_router = APIRouter()

api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(llm.router, prefix="/llm", tags=["LLM"])
api_router.include_router(quality_metrics.router, tags=["Quality Metrics"])
api_router.include_router(world_cup_predictions.router, tags=["World Cup Predictions"])
api_router.include_router(world_cup_analytics.router, tags=["World Cup Analytics"])
api_router.include_router(predictions.router, tags=["Predictions"])
api_router.include_router(sport_markets.router, tags=["Sport Markets"])
api_router.include_router(sport_edges.router, tags=["Sport Edges"])
api_router.include_router(sport_recommendations.router, tags=["Sport Recommendations"])
api_router.include_router(sport_settlements.router, tags=["Sport Settlements"])
api_router.include_router(sport_odds.router, tags=["Sport Odds"])
