"""ClimatePass AI — public REST API (layer 3 of 3)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.errors import register_error_handlers
from api.routers import alerts, meta, routes, segments
from core.config import settings
from core.logging import configure_logging, get_logger

configure_logging("api")
log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Warm the routing graph once, at startup — never per request."""
    settings.ensure_dirs()
    log.info("api_starting", demo_mode=settings.demo_mode)

    try:
        from api.routing.graph import get_graph, graph_status

        started = time.time()
        get_graph()
        log.info("routing_graph_warm", seconds=round(time.time() - started, 1), **graph_status())
    except Exception as exc:  # noqa: BLE001
        # A cold graph must not stop the API booting: /health and the segment
        # endpoints still work, and routing rebuilds on first request.
        log.warning("routing_graph_warm_failed", error=str(exc)[:200])

    yield
    log.info("api_stopped")


app = FastAPI(
    lifespan=lifespan,
    title="ClimatePass AI",
    version="0.1.0",
    description=(
        "Road-network flood exposure for Abuja FCT. Scores every ~100 m road "
        "segment daily from satellite data, returns risk-aware routing, and "
        "alerts on user-defined corridor thresholds.\n\n"
        "See `/v1/meta/model` for the live weights, data provenance and known "
        "limitations."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.api_cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)
app.include_router(routes.router)
app.include_router(segments.router)
app.include_router(alerts.router)
app.include_router(meta.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.time()
    response = await call_next(request)
    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        ms=round((time.time() - started) * 1000, 1),
    )
    return response


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


@app.get("/health/routing", summary="Routing graph status", tags=["meta"])
def health_routing() -> dict:
    from api.routing.graph import graph_status

    return graph_status()
