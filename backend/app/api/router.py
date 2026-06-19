# app/api/router.py — v0.3.0
from fastapi import APIRouter
from app.api.routes import events

api_router = APIRouter()

api_router.include_router(events.router, prefix="/events", tags=["Events"])
