# app/api/router.py — v0.3.0
from fastapi import APIRouter
from app.api.routes import events, quality_metrics, world_cup_predictions, world_cup_analytics

api_router = APIRouter()

api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(quality_metrics.router, tags=["Quality Metrics"])
api_router.include_router(world_cup_predictions.router, tags=["World Cup Predictions"])
api_router.include_router(world_cup_analytics.router, tags=["World Cup Analytics"])
