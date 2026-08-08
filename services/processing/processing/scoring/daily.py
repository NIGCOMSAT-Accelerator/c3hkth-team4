"""P7 Part A — daily flood risk: susceptibility plus rainfall.

Susceptibility says where water collects. Rainfall says whether there is water
to collect. Risk is the two together, and the interaction between them matters
more than either alone.

  python -m processing.scoring.daily --city abuja

RAINFALL PROVENANCE, disclosed rather than glossed
--------------------------------------------------
CHIRPS blends satellite thermal-infrared cold-cloud-duration estimates with
station data, which is what makes it a genuinely space-derived rainfall input.
On DE Africa it runs roughly **54 days behind real time** (measured 2026-08-08:
newest available 2026-06-15). For a 7-day antecedent window on today's date it
therefore contributes nothing, and Open-Meteo supplies all of it.

We do not hide that. Every run records the exact split in ingestion_runs.notes
and it is served at /v1/meta/model, so anyone asking "is this really satellite
data?" gets the true answer from the API rather than from a slide.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import httpx
import numpy as np
import rasterio
from sqlalchemy import select, text

from core.config import get_city, settings
from core.db import session_scope
from core.logging import configure_logging, get_logger
from core.models import City
from processing.common import ingestion_run

configure_logging("processing")
log = get_logger("daily")

STAC_URL = "https://explorer.digitalearth.africa/stac"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# One config dict — MODEL.md and /v1/meta/model both read this.
RISK_WEIGHTS: dict[str, float] = {
    "base": 0.50,
    "wetness": 0.20,
    "interaction": 0.30,
}
WETNESS_SATURATION_MM = 120.0  # 7-day antecedent rain that fully saturates ground
TRIGGER_SATURATION_MM = 50.0  # 24-hour forecast rain that fully triggers
ANTECEDENT_DAYS = 7

BAND_LOW_MAX = 33
BAND_MEDIUM_MAX = 66


# --------------------------------------------------------------------------
# Rainfall
# --------------------------------------------------------------------------


def chirps_daily_mm(
    lon: float, lat: float, start: dt.date, end: dt.date
) -> tuple[dict[dt.date, float], int | None]:
    """Daily CHIRPS totals at a point, plus the feed's latency in days.

    Returns ({} , latency) when the window predates what CHIRPS has published,
    which on this feed is the normal case for recent dates.
    """
    from processing.ingest.wofs import list_collection_ids

    collection = next(
        (c for c in list_collection_ids() if "chirps" in c.lower() and "daily" in c.lower()),
        None,
    )
    if collection is None:
        log.warning("chirps_collection_absent")
        return {}, None
    log.info("chirps_collection_selected", collection=collection)

    def search(a: dt.date, b: dt.date, limit: int = 60) -> list[dict]:
        response = httpx.get(
            f"{STAC_URL}/search",
            params={
                "collections": collection,
                "bbox": f"{lon - 0.1},{lat - 0.1},{lon + 0.1},{lat + 0.1}",
                "datetime": f"{a}T00:00:00Z/{b}T23:59:59Z",
                "limit": limit,
            },
            timeout=90,
        )
        return response.json().get("features", [])

    # How far behind is the feed? Look back far enough to find its newest item.
    latency: int | None = None
    probe = search(end - dt.timedelta(days=120), end, limit=200)
    if probe:
        newest = max(f["properties"]["datetime"][:10] for f in probe)
        latency = (end - dt.date.fromisoformat(newest)).days
        log.info("chirps_latency_measured", newest=newest, days_behind=latency)

    features = search(start, end)
    if not features:
        log.warning(
            "chirps_window_empty",
            window=f"{start}..{end}",
            latency_days=latency,
            note="Open-Meteo will supply the antecedent signal; this is disclosed",
        )
        return {}, latency

    totals: dict[dt.date, float] = {}
    for feature in features:
        day = dt.date.fromisoformat(feature["properties"]["datetime"][:10])
        assets = feature.get("assets", {})
        key = next((k for k in ("data", "rainfall", "precipitation") if k in assets), None)
        if key is None:
            continue
        href = assets[key]["href"]
        if href.startswith("s3://"):
            bucket, _, path = href[5:].partition("/")
            href = f"https://{bucket}.s3.af-south-1.amazonaws.com/{path}"
        try:
            with rasterio.open(f"/vsicurl/{href}") as src:
                value = next(src.sample([(lon, lat)]))[0]
            if np.isfinite(value) and value >= 0:
                totals[day] = float(value)
        except Exception as exc:  # noqa: BLE001 — one bad tile must not kill the run
            log.warning("chirps_read_failed", day=str(day), error=str(exc)[:100])

    log.info("chirps_days_loaded", days=len(totals))
    return totals, latency


def open_meteo(lon: float, lat: float, timezone: str) -> tuple[dict[dt.date, float], float]:
    """Daily past totals and the next 24 h forecast total, both in mm."""
    response = httpx.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "precipitation",
            "past_days": 14,
            "forecast_days": 3,
            "timezone": timezone,
        },
        timeout=60,
    )
    response.raise_for_status()
    hourly = response.json()["hourly"]

    daily: dict[dt.date, float] = {}
    for stamp, value in zip(hourly["time"], hourly["precipitation"], strict=True):
        if value is None:
            continue
        day = dt.date.fromisoformat(stamp[:10])
        daily[day] = daily.get(day, 0.0) + float(value)

    now = dt.datetime.now()
    next_24h = sum(
        value
        for stamp, value in zip(hourly["time"], hourly["precipitation"], strict=True)
        if value is not None and now <= dt.datetime.fromisoformat(stamp) <= now + dt.timedelta(hours=24)
    )
    log.info("open_meteo_loaded", days=len(daily), forecast_24h_mm=round(next_24h, 1))
    return daily, float(next_24h)


def resolve_rainfall(cfg, valid_date: dt.date) -> dict:
    """Merge CHIRPS with Open-Meteo and report exactly where each day came from."""
    lon, lat = cfg.centroid
    start = valid_date - dt.timedelta(days=ANTECEDENT_DAYS - 1)

    chirps, latency = chirps_daily_mm(lon, lat, start, valid_date)
    meteo, forecast_24h = open_meteo(lon, lat, cfg.timezone)

    window = [start + dt.timedelta(days=i) for i in range(ANTECEDENT_DAYS)]
    per_day, from_chirps, filled = {}, 0, 0
    for day in window:
        if day in chirps:
            per_day[day] = chirps[day]
            from_chirps += 1
        elif day in meteo:
            per_day[day] = meteo[day]
            filled += 1

    rain_7d = float(sum(per_day.values()))
    provenance = f"chirps:{from_chirps}d, open-meteo:{filled}d"
    log.info(
        "rainfall_resolved",
        rain_7d_mm=round(rain_7d, 1),
        forecast_24h_mm=round(forecast_24h, 1),
        provenance=provenance,
        chirps_latency_days=latency,
    )
    return {
        "rain_7d_mm": rain_7d,
        "rain_24h_forecast_mm": forecast_24h,
        "provenance": provenance,
        "chirps_days": from_chirps,
        "open_meteo_days": filled,
        "chirps_latency_days": latency,
    }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

SCORE_SQL = """
WITH inputs AS (
    SELECT
        s.id,
        s.susceptibility_pctile / 100.0            AS base,
        LEAST(:rain_7d / :wet_sat, 1.0)            AS wetness,
        LEAST(:rain_24h / :trig_sat, 1.0)          AS trigger
    FROM road_segments s
    WHERE s.city_id = :city_id AND s.susceptibility_pctile IS NOT NULL
),
scored AS (
    SELECT
        id, base, wetness, trigger,
        GREATEST(0.0, LEAST(1.0,
              :w_base * base
            + :w_wet  * wetness
            + :w_int  * (base * trigger)
        )) * 100.0 AS risk
    FROM inputs
)
INSERT INTO segment_risk (
    segment_id, valid_date, risk_score, risk_band,
    rain_7d_mm, rain_24h_forecast_mm, contributions
)
SELECT
    id, :valid_date, risk,
    CASE WHEN risk <= :band_low THEN 'Low'
         WHEN risk <= :band_med THEN 'Medium'
         ELSE 'High' END,
    :rain_7d, :rain_24h,
    jsonb_build_object(
        'base', round(base::numeric, 4),
        'wetness', round(wetness::numeric, 4),
        'trigger', round(trigger::numeric, 4),
        'base_contribution', round((:w_base * base * 100)::numeric, 2),
        'wetness_contribution', round((:w_wet * wetness * 100)::numeric, 2),
        'interaction_contribution', round((:w_int * base * trigger * 100)::numeric, 2),
        -- CAST(...) rather than the double-colon cast operator, which collides
        -- with SQLAlchemy's colon-prefixed bind syntax and leaves it unbound.
        -- (Do not write a colon-prefixed word anywhere in this string, comments
        -- included -- SQLAlchemy scans those too and will demand a value.)
        'rain_7d_mm', round(CAST(:rain_7d AS numeric), 1),
        'rain_24h_forecast_mm', round(CAST(:rain_24h AS numeric), 1),
        -- jsonb_build_object gives Postgres nothing to infer a type from, so
        -- an untyped bind here fails with "could not determine data type".
        'provenance', CAST(:provenance AS text)
    )
FROM scored
ON CONFLICT (segment_id, valid_date) DO UPDATE SET
    risk_score = EXCLUDED.risk_score,
    risk_band = EXCLUDED.risk_band,
    rain_7d_mm = EXCLUDED.rain_7d_mm,
    rain_24h_forecast_mm = EXCLUDED.rain_24h_forecast_mm,
    contributions = EXCLUDED.contributions;
"""


def run(city_slug: str, valid_date: dt.date | None) -> int:
    cfg = get_city(city_slug)
    valid_date = valid_date or dt.date.today()

    with session_scope() as session:
        city_id = session.scalar(select(City.id).where(City.slug == cfg.slug))
    if city_id is None:
        raise SystemExit(f"City {cfg.slug!r} not loaded. Run processing.ingest.roads first.")

    with ingestion_run("daily_risk", city_id) as handle:
        rainfall = resolve_rainfall(cfg, valid_date)

        with session_scope() as session:
            result = session.execute(
                text(SCORE_SQL),
                {
                    "city_id": city_id,
                    "valid_date": valid_date,
                    "rain_7d": rainfall["rain_7d_mm"],
                    "rain_24h": rainfall["rain_24h_forecast_mm"],
                    "wet_sat": WETNESS_SATURATION_MM,
                    "trig_sat": TRIGGER_SATURATION_MM,
                    "w_base": RISK_WEIGHTS["base"],
                    "w_wet": RISK_WEIGHTS["wetness"],
                    "w_int": RISK_WEIGHTS["interaction"],
                    "band_low": BAND_LOW_MAX,
                    "band_med": BAND_MEDIUM_MAX,
                    "provenance": rainfall["provenance"],
                },
            )
            written = result.rowcount

        handle.records = written
        handle.note(
            f"rain_7d={rainfall['rain_7d_mm']:.1f}mm; "
            f"forecast_24h={rainfall['rain_24h_forecast_mm']:.1f}mm; "
            f"source={rainfall['provenance']}; "
            f"chirps_latency_days={rainfall['chirps_latency_days']}"
        )

    with session_scope() as session:
        bands = session.execute(
            text(
                "SELECT risk_band, count(*), round(min(risk_score)::numeric,1),"
                " round(max(risk_score)::numeric,1) FROM segment_risk"
                " WHERE valid_date = :d GROUP BY risk_band"
            ),
            {"d": valid_date},
        ).fetchall()

    print(f"\n  date            : {valid_date}")
    print(f"  antecedent 7d   : {rainfall['rain_7d_mm']:.1f} mm")
    print(f"  forecast 24h    : {rainfall['rain_24h_forecast_mm']:.1f} mm")
    print(f"  rainfall source : {rainfall['provenance']}")
    if rainfall["chirps_latency_days"] is not None:
        print(f"  CHIRPS latency  : {rainfall['chirps_latency_days']} days behind — disclosed at /v1/meta/model")
    print(f"\n  segments scored : {written:,}\n")
    order = {"Low": 0, "Medium": 1, "High": 2}
    for band, count, lo, hi in sorted(bands, key=lambda r: order.get(r[0], 9)):
        print(f"  {band:<8}{count:>8,}  risk {lo}–{hi}")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score daily flood risk.")
    parser.add_argument("--city", default="abuja")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)
    day = dt.date.fromisoformat(args.date) if args.date else None
    return run(args.city, day)


if __name__ == "__main__":
    sys.exit(main())
