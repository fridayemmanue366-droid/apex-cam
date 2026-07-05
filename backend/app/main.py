"""FastAPI application factory and entrypoint.

Run with:  uvicorn app.main:app --reload --port 8790
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import (
    audio,
    background,
    face,
    models,
    pipeline as pipeline_routes,
    setup,
    system,
    voice,
)
from app.core.audio_pipeline import audio_pipeline
from app.core.demo_faces import ensure_demo_faces
from app.core.logging import get_logger
from app.core.pipeline import pipeline
from app.config import settings

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Apex Cam backend starting (env=%s, version=%s)", settings.env, __version__)
    ensure_demo_faces()
    yield
    pipeline.stop()
    audio_pipeline.stop()
    log.info("Apex Cam backend shutting down")


def create_app() -> FastAPI:
    app = FastAPI(title="Apex Cam Backend", version=__version__, lifespan=lifespan)

    # The desktop renderer calls us over loopback. In dev the origin is the Vite
    # server (localhost:5173); in the packaged app the UI loads from file://,
    # whose fetches send `Origin: null`. Since the backend binds to 127.0.0.1
    # only (not remotely reachable), allow any origin so both cases work.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system.router)
    app.include_router(face.router)
    app.include_router(voice.router)
    app.include_router(models.router)
    app.include_router(pipeline_routes.router)
    app.include_router(audio.router)
    app.include_router(setup.router)
    app.include_router(background.router)
    return app


app = create_app()
