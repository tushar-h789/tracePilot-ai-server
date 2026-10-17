"""Aggregates all v1 routers. New modules register here (auth, orgs, projects, ... later)."""

from fastapi import APIRouter

from app.api.v1.routes import health

api_router = APIRouter()
api_router.include_router(health.router)
