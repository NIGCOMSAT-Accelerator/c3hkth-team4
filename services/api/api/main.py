"""ClimatePass AI — public REST API.

P0 ships /health only. P7 adds segments, point risk, routing, alerts,
subscriptions, geocoding and the /v1/meta/model transparency endpoint.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.logging import configure_logging, get_logger

configure_logging("api")
log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown.

    P7 loads the OSMnx routing graph here — once, held in memory, refreshed on
    a 15-minute timer. Never per request.
    """
    settings.ensure_dirs()
    log.info("api_started", demo_mode=settings.demo_mode, data_dir=str(settings.data_dir))
    yield
    log.info("api_stopped")


app = FastAPI(
    lifespan=lifespan,
    title="ClimatePass AI",
    version="0.1.0",
    description=(
        "Road-network flood exposure for Abuja FCT. Scores every ~100m road "
        "segment daily from satellite data, returns risk-aware routing, and "
        "alerts on user-defined corridor thresholds."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.api_cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", summary="Liveness probe", tags=["meta"])
def health() -> dict[str, str]:
    """Returns ok as soon as the process is serving. No dependencies checked."""
    return {"status": "ok"}


@app.get("/health/db", summary="Database and PostGIS readiness", tags=["meta"])
def health_db() -> dict[str, object]:
    """Separate from /health so a DB blip never fails the container's liveness."""
    from sqlalchemy import text

    from core.db import engine

    try:
        with engine.connect() as conn:
            postgis = conn.execute(text("SELECT PostGIS_Version()")).scalar_one()
        return {"status": "ok", "postgis": postgis}
    except Exception as exc:  # noqa: BLE001 - surface the real reason in dev
        log.warning("db_health_failed", error=str(exc))
        return {"status": "degraded", "error": str(exc)}
