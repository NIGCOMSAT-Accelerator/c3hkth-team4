"""Geocoding and the model transparency endpoint."""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.errors import ApiError
from api.routing.engine import SEVERE_RISK_MULTIPLIER, SEVERE_RISK_THRESHOLD
from core.config import get_city, settings
from core.db import get_session
from core.logging import get_logger
from core.model import (
    ANTECEDENT_DAYS,
    BAND_LOW_MAX,
    BAND_MEDIUM_MAX,
    FEATURES,
    INTERACTION_RATIONALE,
    LIMITATIONS,
    RISK_FORMULA,
    RISK_WEIGHTS,
    SUSCEPTIBILITY_WEIGHTS,
    TRIGGER_SATURATION_MM,
    WETNESS_SATURATION_MM,
    WOFS_BUFFER_M,
)

router = APIRouter(prefix="/v1", tags=["meta"])
log = get_logger("api.meta")

_geocode_cache: dict[str, list[dict]] = {}


@router.get(
    "/geocode",
    summary="Resolve a place name to coordinates",
    description=(
        "Searches a bundled gazetteer of Abuja landmarks first, then falls back to "
        "OSM Nominatim. The gazetteer is authoritative for the demo: a live "
        "geocoder rate-limiting us on stage must not break the product."
    ),
)
def geocode(
    q: str = Query(..., min_length=2, examples=["Lugbe"]),
    city: str = "abuja",
) -> dict:
    key = f"{city}:{q.lower().strip()}"
    if key in _geocode_cache:
        return {"query": q, "results": _geocode_cache[key], "cached": True}

    cfg = get_city(city)
    needle = q.lower().strip()

    results = [
        {"name": lm["name"], "lat": lm["lat"], "lon": lm["lon"], "source": "gazetteer"}
        for lm in cfg.landmarks
        if needle in lm["name"].lower()
    ]

    if not results and not settings.demo_mode:
        try:
            min_lon, min_lat, max_lon, max_lat = cfg.bbox
            response = httpx.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": q, "format": "json", "limit": 5,
                    "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}",
                    "bounded": 1,
                },
                headers={"User-Agent": "ClimatePassAI/0.1 (hackathon)"},
                timeout=8,
            )
            response.raise_for_status()
            results = [
                {
                    "name": item["display_name"].split(",")[0],
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                    "source": "osm",
                }
                for item in response.json()
            ]
        except Exception as exc:  # noqa: BLE001 — degrade to gazetteer, never fail the UI
            log.warning("nominatim_unavailable", error=str(exc)[:120])

    if results:
        _geocode_cache[key] = results
    return {"query": q, "results": results, "cached": False}


@router.get(
    "/meta/model",
    summary="How the risk score is computed",
    description=(
        "Serves the live weights, features, formula, data provenance and known "
        "limitations. These are the same constants the pipeline applies — the API "
        "reads packages/core/core/model.py, so published weights cannot drift from "
        "applied weights."
    ),
)
def model_card(session: Session = Depends(get_session)) -> dict:
    runs = session.execute(
        text(
            "SELECT source, status, records, notes, started_at FROM ingestion_runs"
            " ORDER BY id DESC LIMIT 12"
        )
    ).fetchall()

    rainfall_note = next(
        (r.notes for r in runs if r.source == "daily_risk" and r.notes), None
    )

    validation = None
    validation_path = settings.city_derived_dir("abuja") / "validation.json"
    if validation_path.exists():
        validation = json.loads(validation_path.read_text())

    return {
        "susceptibility": {
            "weights": SUSCEPTIBILITY_WEIGHTS,
            "features": FEATURES,
            "wofs_buffer_m": WOFS_BUFFER_M,
            "note": "All terms are percentile-ranked across the city before weighting, "
                    "so the index is a relative ordering of this road network rather "
                    "than an absolute depth prediction.",
        },
        "daily_risk": {
            "weights": RISK_WEIGHTS,
            "formula": RISK_FORMULA,
            "interaction_rationale": INTERACTION_RATIONALE,
            "wetness_saturation_mm": WETNESS_SATURATION_MM,
            "trigger_saturation_mm": TRIGGER_SATURATION_MM,
            "antecedent_days": ANTECEDENT_DAYS,
            "bands": {
                "Low": [0, BAND_LOW_MAX],
                "Medium": [BAND_LOW_MAX + 1, BAND_MEDIUM_MAX],
                "High": [BAND_MEDIUM_MAX + 1, 100],
            },
        },
        "route_risk": {
            "formula": "0.6 * max(segment_risk) + 0.4 * length_weighted_mean(segment_risk)",
            "rationale": "One impassable segment ruins a route; a plain mean would hide it.",
            "safest_route_cost": "travel_time * (1 + lambda * (risk/100)^2), lambda default 3.0",
            "severe_risk_threshold": SEVERE_RISK_THRESHOLD,
            "severe_risk_multiplier": SEVERE_RISK_MULTIPLIER,
            "severe_rationale": (
                "Segments at or above the severe threshold cost 50x rather than "
                "being deleted: deleting can disconnect the graph and return no "
                "route, which is a worse answer than a bad route clearly marked. "
                "The threshold matches the High band so the router acts on what "
                "the map calls High."
            ),
        },
        "rainfall_provenance": rainfall_note,
        "validation": validation or {
            "status": "not_run",
            "note": "Sentinel-1 validation (P6) has not been run for this deployment.",
        },
        "limitations": LIMITATIONS,
        "recent_ingestion_runs": [
            {
                "source": r.source,
                "status": r.status,
                "records": r.records,
                "notes": r.notes,
                "started_at": str(r.started_at),
            }
            for r in runs
        ],
    }
