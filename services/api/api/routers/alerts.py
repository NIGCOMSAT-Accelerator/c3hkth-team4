"""Active high-risk clusters and Corridor Watch subscriptions."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from geoalchemy2.shape import from_shape
from shapely.geometry import LineString
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from api.errors import ApiError
from api.schemas import SubscriptionCreate, SubscriptionOut
from core.db import get_session
from core.models import City, Subscription

router = APIRouter(prefix="/v1", tags=["alerts"])


@router.get(
    "/alerts",
    summary="Today's highest-risk corridors",
    description=(
        "Named roads ranked by peak risk today. Powers the Home page so it is "
        "never empty, and the Alerts page's cluster cards."
    ),
)
def active_alerts(limit: int = 10, session: Session = Depends(get_session)) -> dict:
    target = session.scalar(text("SELECT max(valid_date) FROM segment_risk"))
    if target is None:
        raise ApiError("no_risk_data", "No risk has been scored yet.", 503)

    rows = session.execute(
        text(
            "SELECT s.name, s.highway_class, max(sr.risk_score) AS peak,"
            "  avg(sr.risk_score) AS mean, count(*) AS segments,"
            "  round(avg(s.hand_min)::numeric,1) AS hand,"
            "  ST_AsGeoJSON(ST_Centroid(ST_Collect(s.geom))) AS centroid"
            " FROM road_segments s JOIN segment_risk sr ON sr.segment_id = s.id"
            " WHERE sr.valid_date = :d AND s.name IS NOT NULL"
            " GROUP BY s.name, s.highway_class"
            " ORDER BY max(sr.risk_score) DESC, avg(sr.risk_score) DESC LIMIT :lim"
        ),
        {"d": target, "lim": limit},
    ).fetchall()

    import json

    return {
        "valid_date": str(target),
        "count": len(rows),
        "clusters": [
            {
                "name": r.name,
                "highway_class": r.highway_class,
                "peak_risk": round(r.peak, 1),
                "mean_risk": round(r.mean, 1),
                "segments": r.segments,
                "hand_min_m": float(r.hand) if r.hand is not None else None,
                "centroid": json.loads(r.centroid),
            }
            for r in rows
        ],
    }


@router.post(
    "/subscriptions",
    summary="Create a Corridor Watch",
    status_code=201,
    description=(
        "Watch a corridor and be alerted when its risk crosses your threshold. "
        "Evaluated every 30 minutes by the alerts service, debounced to at most "
        "one alert per subscription per 6 hours."
    ),
)
def create_subscription(
    payload: SubscriptionCreate, session: Session = Depends(get_session)
) -> SubscriptionOut:
    city_id = session.scalar(select(City.id).where(City.slug == payload.city))
    if city_id is None:
        raise ApiError("unknown_city", f"City {payload.city!r} is not loaded.", 422)

    if payload.channel == "webhook" and not payload.webhook_url:
        raise ApiError(
            "missing_webhook_url",
            "channel='webhook' requires webhook_url. Supply one, or use channel='email'.",
            422,
        )

    line = LineString([(p.lon, p.lat) for p in payload.corridor])
    subscription = Subscription(
        email=payload.email,
        city_id=city_id,
        corridor=from_shape(line, srid=4326),
        corridor_name=payload.corridor_name,
        threshold=payload.threshold,
        channel=payload.channel,
        webhook_url=payload.webhook_url,
        active=True,
    )
    session.add(subscription)
    session.commit()
    session.refresh(subscription)
    return SubscriptionOut(
        id=subscription.id,
        email=subscription.email,
        corridor_name=subscription.corridor_name,
        threshold=subscription.threshold,
        channel=subscription.channel,
        active=subscription.active,
        created_at=subscription.created_at,
    )


@router.get("/subscriptions/{subscription_id}", summary="Fetch a Corridor Watch")
def get_subscription(
    subscription_id: int, session: Session = Depends(get_session)
) -> dict:
    subscription = session.get(Subscription, subscription_id)
    if subscription is None:
        raise ApiError("not_found", f"No subscription with id {subscription_id}.", 404)

    fired = session.execute(
        text(
            "SELECT id, triggered_at, risk_score, delivered FROM alerts"
            " WHERE subscription_id = :sid ORDER BY triggered_at DESC LIMIT 10"
        ),
        {"sid": subscription_id},
    ).fetchall()

    return {
        "id": subscription.id,
        "email": subscription.email,
        "corridor_name": subscription.corridor_name,
        "threshold": subscription.threshold,
        "channel": subscription.channel,
        "active": subscription.active,
        "created_at": str(subscription.created_at),
        "recent_alerts": [
            {
                "id": a.id,
                "triggered_at": str(a.triggered_at),
                "risk_score": round(a.risk_score, 1) if a.risk_score else None,
                "delivered": a.delivered,
            }
            for a in fired
        ],
    }
