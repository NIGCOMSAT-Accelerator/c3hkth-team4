"""P5 stage A — PLACEHOLDER susceptibility, so the API and frontend can start.

These numbers are not evidence. They exist so tracks B and C can develop
against real rows within 30 minutes of the road network landing, instead of
waiting on the terrain pipeline. susceptibility_v2 overwrites this column in
place from HAND, WOfS, slope and drainage; nothing downstream needs to know
which stage produced the value, which is the entire point of writing to the
same column.

  python -m processing.scoring.susceptibility_v1 --city abuja

Every run logs a PLACEHOLDER warning. If you see that warning during the
demo, something has gone wrong.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select, text

from core.config import get_city
from core.db import session_scope
from core.logging import configure_logging, get_logger
from core.models import City, RoadSegment
from processing.common import ingestion_run

configure_logging("processing")
log = get_logger("susceptibility_v1")

# Crude priors by road class. Minor and residential roads flood more readily
# than engineered trunk routes: thinner surfacing, shallower drainage, less
# maintenance. Real evidence replaces this in v2.
CLASS_WEIGHTS = {
    "motorway": 0.20,
    "motorway_link": 0.25,
    "trunk": 0.30,
    "trunk_link": 0.35,
    "primary": 0.35,
    "primary_link": 0.40,
    "secondary": 0.45,
    "secondary_link": 0.50,
    "tertiary": 0.55,
    "tertiary_link": 0.55,
    "unclassified": 0.65,
    "residential": 0.70,
    "living_street": 0.70,
    "service": 0.75,
    "track": 0.85,
}
DEFAULT_CLASS_WEIGHT = 0.60

# A smooth spatial field so the map shows coherent clusters rather than
# salt-and-pepper noise, which would look obviously fake in a screenshot.
# Deterministic: same segment ids in, same picture out.
SCORE_SQL = """
WITH scored AS (
    SELECT
        s.id,
        LEAST(1.0, GREATEST(0.0,
              0.45 * {class_case}
            + 0.35 * (
                0.5 + 0.25 * sin(ST_X(ST_Centroid(s.geom)) * 47.0)
                    + 0.25 * cos(ST_Y(ST_Centroid(s.geom)) * 53.0)
              )
            + 0.20 * (((s.id * 2654435761) % 1000)::float / 1000.0)
        )) AS susceptibility
    FROM road_segments s
    WHERE s.city_id = :city_id
),
ranked AS (
    SELECT id, susceptibility,
           100.0 * percent_rank() OVER (ORDER BY susceptibility) AS pctile
    FROM scored
)
UPDATE road_segments rs
SET susceptibility = r.susceptibility,
    susceptibility_pctile = r.pctile,
    features_updated_at = now()
FROM ranked r
WHERE rs.id = r.id;
"""


def _class_case() -> str:
    """Render CLASS_WEIGHTS as a SQL CASE so the whole update stays server-side."""
    whens = " ".join(
        f"WHEN '{cls}' THEN {weight}" for cls, weight in CLASS_WEIGHTS.items()
    )
    return f"(CASE s.highway_class {whens} ELSE {DEFAULT_CLASS_WEIGHT} END)"


def score(city_slug: str) -> int:
    cfg = get_city(city_slug)

    with session_scope() as session:
        city_id = session.scalar(select(City.id).where(City.slug == cfg.slug))
        if city_id is None:
            raise SystemExit(
                f"City {cfg.slug!r} is not loaded. Run processing.ingest.roads first."
            )
        total = session.scalar(
            select(RoadSegment.id).where(RoadSegment.city_id == city_id).limit(1)
        )
        if total is None:
            raise SystemExit(
                f"No road segments for {cfg.slug!r}. Run processing.ingest.roads first."
            )

    log.warning(
        "PLACEHOLDER_SCORING",
        detail="susceptibility_v1 writes crude values to unblock tracks B and C. "
        "These must be replaced by susceptibility_v2 before the demo.",
    )

    with ingestion_run("susceptibility_v1", city_id) as handle:
        with session_scope() as session:
            result = session.execute(
                text(SCORE_SQL.format(class_case=_class_case())), {"city_id": city_id}
            )
            updated = result.rowcount
        handle.records = updated
        handle.note("PLACEHOLDER values — replaced by susceptibility_v2")

    with session_scope() as session:
        nulls = session.scalar(
            text(
                "SELECT count(*) FROM road_segments "
                "WHERE city_id = :cid AND susceptibility IS NULL"
            ),
            {"cid": city_id},
        )
        lo, med, hi = session.execute(
            text(
                "SELECT min(susceptibility), "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY susceptibility), "
                "max(susceptibility) FROM road_segments WHERE city_id = :cid"
            ),
            {"cid": city_id},
        ).one()

    # NULLs crash the router — hard rule 5.
    if nulls:
        raise SystemExit(f"{nulls} segments still have NULL susceptibility.")

    print(f"\n  segments scored: {updated:,}")
    print(f"  susceptibility:  min {lo:.3f}  median {med:.3f}  max {hi:.3f}")
    print("  NULLs:           0")
    print("\n  *** PLACEHOLDER VALUES — not evidence. Run susceptibility_v2 ***\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="abuja")
    args = parser.parse_args(argv)
    return score(args.city)


if __name__ == "__main__":
    sys.exit(main())
