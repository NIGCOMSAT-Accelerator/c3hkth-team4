"""Which scoring date the API serves.

One helper so every endpoint and the routing graph agree. Without it, the map
can show one date while the router uses another, which is the kind of
inconsistency nobody notices until it is on a projector.

DEMO_DATE pins the served date to a known-good scoring run. That is P10's
"frozen scenario date" requirement, and it is why the demo does not depend on
whether it happened to rain in Abuja the morning of the pitch.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text

from core.config import settings
from core.logging import get_logger

log = get_logger("api.dates")


def resolve_valid_date(session, explicit: dt.date | None = None) -> dt.date | None:
    """Explicit request date, else DEMO_DATE, else the most recent scored date."""
    if explicit is not None:
        return explicit

    if settings.demo_date:
        pinned = dt.date.fromisoformat(settings.demo_date)
        exists = session.scalar(
            text("SELECT 1 FROM segment_risk WHERE valid_date = :d LIMIT 1"), {"d": pinned}
        )
        if exists:
            return pinned
        # Never fail closed on a misconfigured pin — fall through to latest and
        # say so loudly, so a bad DEMO_DATE is visible in the logs, not silent.
        log.warning("demo_date_has_no_data", demo_date=settings.demo_date)

    return session.scalar(text("SELECT max(valid_date) FROM segment_risk"))
