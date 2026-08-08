"""P9 — evaluate Corridor Watch subscriptions against current risk.

Layer 2 of the three-layer separation. This service owns thresholds,
subscriptions and dispatch, and it shares nothing with the processing or API
layers except packages/core.

  python -m alerts.evaluate --now --force

--now --force exists so an alert can be fired on demand during a live demo,
which is a thing you want at your fingertips on stage, not something you
discover you cannot do while a judge watches.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from sqlalchemy import select, text

from core.db import session_scope
from core.logging import configure_logging, get_logger
from core.models import Alert, Subscription

configure_logging("alerts")
log = get_logger("alerts.evaluate")

CORRIDOR_BUFFER_M = 50.0

# Alert spam destroys trust faster than missing an alert does: a user who gets
# six notifications about one storm mutes the channel and misses the next one.
DEBOUNCE_HOURS = 6

CORRIDOR_SQL = """
SELECT s.id, s.name, s.highway_class, sr.risk_score, sr.risk_band,
       sr.contributions, s.hand_min
FROM road_segments s
JOIN segment_risk sr ON sr.segment_id = s.id
WHERE sr.valid_date = :valid_date
  AND ST_DWithin(
        s.geom::geography,
        (SELECT corridor FROM subscriptions WHERE id = :sub_id)::geography,
        :buffer_m
      )
ORDER BY sr.risk_score DESC
LIMIT 1
"""


def build_message(corridor_name: str, segment: dict, threshold: int) -> str:
    """A short, email-appropriate sentence built from the evidence.

    Deliberately not imported from the API's explain module: layer 2 must not
    depend on layer 3, and an alert reads differently from a UI panel anyway.
    """
    where = segment["name"] or "an unnamed section"
    parts = [
        f"{corridor_name} has reached risk {segment['risk_score']:.0f}/100 "
        f"({segment['risk_band']}), above your threshold of {threshold}."
    ]
    parts.append(f"The riskiest point is {where}.")

    if segment.get("hand_min") is not None and segment["hand_min"] <= 5:
        parts.append(
            f"It sits {segment['hand_min']:.1f} m above the nearest drainage "
            "channel, so it floods before the surrounding area does."
        )

    contributions = segment.get("contributions") or {}
    rain_7d = contributions.get("rain_7d_mm")
    rain_24h = contributions.get("rain_24h_forecast_mm")
    if rain_7d is not None:
        sentence = f"{float(rain_7d):.0f} mm has fallen in the past week"
        if rain_24h:
            sentence += f", with {float(rain_24h):.0f} mm more forecast in 24 hours"
        parts.append(sentence + ".")

    if contributions.get("scenario"):
        parts.append(
            "NOTE: this evaluation used SCENARIO rainfall, not observed rainfall."
        )

    return " ".join(parts)


def evaluate_subscription(session, subscription: Subscription, valid_date, force: bool) -> dict | None:
    """Return an alert payload if this corridor has crossed its threshold."""
    row = session.execute(
        text(CORRIDOR_SQL),
        {"sub_id": subscription.id, "valid_date": valid_date, "buffer_m": CORRIDOR_BUFFER_M},
    ).first()

    if row is None:
        log.info("no_segments_on_corridor", subscription=subscription.id)
        return None

    segment = {
        "id": row.id,
        "name": row.name,
        "highway_class": row.highway_class,
        "risk_score": float(row.risk_score),
        "risk_band": row.risk_band,
        "contributions": row.contributions,
        "hand_min": float(row.hand_min) if row.hand_min is not None else None,
    }

    if segment["risk_score"] < subscription.threshold and not force:
        log.info(
            "below_threshold",
            subscription=subscription.id,
            risk=round(segment["risk_score"], 1),
            threshold=subscription.threshold,
        )
        return None

    if not force:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=DEBOUNCE_HOURS)
        recent = session.scalar(
            select(Alert.id)
            .where(Alert.subscription_id == subscription.id, Alert.triggered_at >= cutoff)
            .limit(1)
        )
        if recent:
            log.info("debounced", subscription=subscription.id, window_hours=DEBOUNCE_HOURS)
            return None

    corridor_name = subscription.corridor_name or "Your watched corridor"
    from core.config import settings

    return {
        "subscription_id": subscription.id,
        "email": subscription.email,
        "corridor_name": corridor_name,
        "threshold": subscription.threshold,
        "risk_score": round(segment["risk_score"], 1),
        "risk_band": segment["risk_band"],
        "segment_id": segment["id"],
        "segment_name": segment["name"],
        "highway_class": segment["highway_class"],
        "hand_min_m": segment["hand_min"],
        "valid_date": str(valid_date),
        "message": build_message(corridor_name, segment, subscription.threshold),
        "link": f"{settings.web_base_url}/results?segment={segment['id']}",
        "contributions": segment["contributions"],
    }


def run(now: bool, force: bool, subscription_id: int | None = None) -> int:
    from alerts.deliver import deliver

    with session_scope() as session:
        valid_date = session.scalar(text("SELECT max(valid_date) FROM segment_risk"))
        if valid_date is None:
            log.error("no_risk_data", note="run the daily scoring pipeline first")
            return 1

        query = select(Subscription).where(Subscription.active.is_(True))
        if subscription_id:
            query = query.where(Subscription.id == subscription_id)
        subscriptions = list(session.scalars(query))

    log.info(
        "evaluation_start",
        subscriptions=len(subscriptions),
        valid_date=str(valid_date),
        force=force,
    )

    fired = 0
    for subscription in subscriptions:
        with session_scope() as session:
            subscription = session.get(Subscription, subscription.id)
            payload = evaluate_subscription(session, subscription, valid_date, force)
            if payload is None:
                continue

            alert = Alert(
                subscription_id=subscription.id,
                segment_id=payload["segment_id"],
                risk_score=payload["risk_score"],
                payload=payload,
                delivered=False,
            )
            session.add(alert)
            session.flush()
            alert_id = alert.id
            channel = subscription.channel
            webhook_url = subscription.webhook_url

        result = deliver(payload, channel, webhook_url)
        with session_scope() as session:
            alert = session.get(Alert, alert_id)
            alert.delivered = result["delivered"]
            merged = dict(alert.payload or {})
            merged["delivery"] = result
            alert.payload = merged

        fired += 1
        log.info(
            "alert_fired",
            alert_id=alert_id,
            subscription=subscription.id,
            risk=payload["risk_score"],
            delivery=result["mode"],
        )

    log.info("evaluation_finished", fired=fired, evaluated=len(subscriptions))
    print(f"\n  evaluated {len(subscriptions)} subscription(s), fired {fired} alert(s)\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Corridor Watch subscriptions.")
    parser.add_argument("--now", action="store_true", help="Evaluate immediately.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the threshold and the debounce window. For live demos.",
    )
    parser.add_argument("--subscription", type=int, default=None)
    args = parser.parse_args(argv)
    return run(args.now, args.force, args.subscription)


if __name__ == "__main__":
    sys.exit(main())
