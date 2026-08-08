"""Segment risk: map layers and point queries."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.errors import ApiError
from api.explain import explain_segment
from core.db import get_session

router = APIRouter(prefix="/v1", tags=["risk"])

MAX_FEATURES = 5000


@router.get(
    "/segments",
    summary="Road segments with today's risk, as GeoJSON",
    description=(
        "Returns a FeatureCollection for a bounding box. Capped at 5000 features; "
        "when the cap is hit `truncated` is true and you should either zoom in or "
        "raise `min_risk`."
    ),
)
def segments(
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat", examples=["7.45,9.03,7.53,9.09"]),
    min_risk: float = Query(0, ge=0, le=100),
    date: dt.date | None = Query(None, description="Defaults to the most recent scored date."),
    session: Session = Depends(get_session),
) -> dict:
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox.split(","))
    except ValueError as exc:
        raise ApiError(
            "invalid_bbox",
            "bbox must be four comma-separated numbers: min_lon,min_lat,max_lon,max_lat",
            422,
            {"received": bbox},
        ) from exc

    target = date or session.scalar(text("SELECT max(valid_date) FROM segment_risk"))
    if target is None:
        raise ApiError(
            "no_risk_data",
            "No risk has been scored yet. Run the daily scoring pipeline.",
            503,
        )

    rows = session.execute(
        text(
            "SELECT s.id, s.name, s.highway_class, sr.risk_score, sr.risk_band,"
            "  ST_AsGeoJSON(s.geom) AS geojson"
            " FROM road_segments s JOIN segment_risk sr ON sr.segment_id = s.id"
            " WHERE sr.valid_date = :d AND sr.risk_score >= :min_risk"
            "   AND s.geom && ST_MakeEnvelope(:x1,:y1,:x2,:y2,4326)"
            " ORDER BY sr.risk_score DESC LIMIT :lim"
        ),
        {
            "d": target, "min_risk": min_risk, "lim": MAX_FEATURES + 1,
            "x1": min_lon, "y1": min_lat, "x2": max_lon, "y2": max_lat,
        },
    ).fetchall()

    truncated = len(rows) > MAX_FEATURES
    rows = rows[:MAX_FEATURES]

    import json

    return {
        "type": "FeatureCollection",
        "valid_date": str(target),
        "truncated": truncated,
        "count": len(rows),
        "features": [
            {
                "type": "Feature",
                "id": r.id,
                "geometry": json.loads(r.geojson),
                "properties": {
                    "name": r.name,
                    "highway_class": r.highway_class,
                    "risk": round(r.risk_score, 1),
                    "band": r.risk_band,
                },
            }
            for r in rows
        ],
    }


@router.get(
    "/risk/point",
    summary="Risk at the road segment nearest a point",
    description=(
        "Returns the nearest segment's risk with its per-factor evidence and a "
        "deterministic template explanation. No LLM is involved: every clause is "
        "traceable to a number in `contributions`."
    ),
)
def risk_point(
    lat: float = Query(..., ge=-90, le=90, examples=[9.0579]),
    lon: float = Query(..., ge=-180, le=180, examples=[7.4913]),
    session: Session = Depends(get_session),
) -> dict:
    target = session.scalar(text("SELECT max(valid_date) FROM segment_risk"))
    if target is None:
        raise ApiError("no_risk_data", "No risk has been scored yet.", 503)

    row = session.execute(
        text(
            "SELECT s.id, s.name, s.highway_class, s.susceptibility, s.hand_min,"
            "  s.wofs_freq_max, s.crosses_drainage, sr.risk_score, sr.risk_band,"
            "  sr.contributions,"
            "  ST_Distance(s.geom::geography, ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography) AS dist"
            " FROM road_segments s JOIN segment_risk sr ON sr.segment_id = s.id"
            " WHERE sr.valid_date = :d"
            " ORDER BY s.geom <-> ST_SetSRID(ST_MakePoint(:lon,:lat),4326) LIMIT 1"
        ),
        {"lon": lon, "lat": lat, "d": target},
    ).first()

    if row is None:
        raise ApiError("no_segment", "No road segment found near that point.", 404)

    explanation, evidence = explain_segment(
        name=row.name,
        risk_score=row.risk_score,
        risk_band=row.risk_band,
        contributions=row.contributions,
        hand_min=row.hand_min,
        wofs_freq_max=row.wofs_freq_max,
        crosses_drainage=row.crosses_drainage,
    )

    return {
        "segment_id": row.id,
        "name": row.name,
        "highway_class": row.highway_class,
        "risk_score": round(row.risk_score, 1),
        "risk_band": row.risk_band,
        "valid_date": str(target),
        "distance_m": round(row.dist, 1),
        "susceptibility": round(row.susceptibility, 3) if row.susceptibility else None,
        "hand_min": round(row.hand_min, 1) if row.hand_min is not None else None,
        "wofs_freq_max": round(row.wofs_freq_max, 4) if row.wofs_freq_max is not None else None,
        "contributions": row.contributions,
        "explanation": explanation,
        "evidence": [e.model_dump() for e in evidence],
    }
