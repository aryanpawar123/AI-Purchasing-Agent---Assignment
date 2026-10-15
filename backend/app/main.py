"""
FastAPI application entrypoint for AI Purchasing Agent.

Exposes:
- Mock ERP router (/erp/*)
- Agent API router (/agent/*)
- Health check (/health)
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.mock_erp.api import router as erp_router
from app.agent.api import router as agent_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AI Purchasing Agent",
    description="Autonomous purchasing decision agent for quick-commerce",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(erp_router)
app.include_router(agent_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "AI Purchasing Agent"}
