# app/api/router.py — v0.3.0
from fastapi import APIRouter
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions, sport_markets

api_router = APIRouter()

api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(llm.router, prefix="/llm", tags=["LLM"])
api_router.include_router(quality_metrics.router, tags=["Quality Metrics"])
api_router.include_router(world_cup_predictions.router, tags=["World Cup Predictions"])
api_router.include_router(world_cup_analytics.router, tags=["World Cup Analytics"])
api_router.include_router(predictions.router, tags=["Predictions"])
api_router.include_router(sport_markets.router, tags=["Sport Markets"])
