"""FastAPI app entrypoint. ECS / docker compose runs:
    uvicorn osint.api.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from osint.api.routes import agents, health


def create_app() -> FastAPI:
    app = FastAPI(title="agent-osint", version="0.2.0")
    # Local dev only. Production goes through ALB on the same origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(agents.router)
    return app


app = create_app()
