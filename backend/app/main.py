"""
FastAPI application entrypoint.

Phase 1: just DB init and health check.
Routes for the agent API and mock ERP will be added in later phases.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.mock_erp.api import router as erp_router

app = FastAPI(
    title="AI Purchasing Agent",
    description="Autonomous purchasing decision agent for quick-commerce",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


app.include_router(erp_router)


@app.get("/health")
def health():
    return {"status": "ok"}
