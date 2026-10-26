from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.routes import router
from .core.config import Settings, get_settings
from .database.db import make_session_factory
from .engines.setup import register_all
from .workers.jobs import JobQueue


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()
    logging.basicConfig(level=logging.INFO)
    register_all(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(title="Leal Dub Studio", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = make_session_factory(f"sqlite:///{settings.db_path}")
    app.state.jobs = JobQueue()
    # Protección: solo peticiones dirigidas a localhost (mitiga DNS rebinding) y orígenes de la app.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:1420", "tauri://localhost",
                                                      "http://tauri.localhost", "http://127.0.0.1:1420"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def close_db_sessions(request, call_next):
        try:
            return await call_next(request)
        finally:
            for session in request.scope.get("db_sessions", []):
                session.close()

    app.include_router(router, prefix="/api")
    return app


if __name__ == "__main__":
    import uvicorn
    cfg = get_settings()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port)
