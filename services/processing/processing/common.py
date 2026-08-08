"""Shared plumbing for every ingestion and scoring stage.

Hard rule 6 in NOTES.md: every pipeline stage writes an ingestion_runs row.
That table is a demo asset — we show it to judges to prove the ingestion is
automated and repeatable. One helper here means five pipeline modules cannot
drift into five different conventions.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from geoalchemy2.shape import from_shape
from shapely.geometry import Point, box
from sqlalchemy import select

from core.config import City as CityConfig
from core.db import session_scope
from core.logging import get_logger
from core.models import City, IngestionRun

log = get_logger("processing")


@dataclass
class RunHandle:
    """Mutable handle a pipeline stage uses to report what it did."""

    records: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        """Record something the pitch must disclose (e.g. a CHIRPS gap fill)."""
        self.notes.append(message)
        log.info("run_note", note=message)


@contextmanager
def ingestion_run(source: str, city_id: int | None = None) -> Iterator[RunHandle]:
    """Bracket a pipeline stage with an ingestion_runs row.

    Failures are recorded too, then re-raised — a stage that dies silently is
    worse than one that dies loudly, and the run table is where we look first.
    """
    handle = RunHandle()
    with session_scope() as session:
        run = IngestionRun(source=source, city_id=city_id, status="running")
        session.add(run)
        session.flush()
        run_id = run.id

    log.info("stage_started", source=source, run_id=run_id)
    started = dt.datetime.now(dt.UTC)

    try:
        yield handle
    # BaseException, not Exception: the scope guard and every other CLI abort
    # raise SystemExit, which does NOT inherit from Exception. Catching only
    # Exception leaves the row stuck in 'running' forever — and this table is
    # something we put in front of judges. KeyboardInterrupt is recorded for
    # the same reason.
    except BaseException as exc:
        with session_scope() as session:
            run = session.get(IngestionRun, run_id)
            run.status = "failed"
            run.finished_at = dt.datetime.now(dt.UTC)
            run.records = handle.records
            run.notes = "; ".join([*handle.notes, f"ERROR: {exc}"])[:4000]
        log.error("stage_failed", source=source, run_id=run_id, error=str(exc))
        raise
    else:
        elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
        with session_scope() as session:
            run = session.get(IngestionRun, run_id)
            run.status = "success"
            run.finished_at = dt.datetime.now(dt.UTC)
            run.records = handle.records
            run.notes = "; ".join(handle.notes)[:4000] or None
        log.info(
            "stage_finished",
            source=source,
            run_id=run_id,
            records=handle.records,
            seconds=round(elapsed, 1),
        )


def ensure_city(cfg: CityConfig, variant: str | None = None) -> int:
    """Insert or update the city row from cities.yaml. Returns its id.

    cities.yaml is the single source of truth; the table mirrors it so that
    spatial queries can join against a real geometry.
    """
    min_lon, min_lat, max_lon, max_lat = cfg.bbox_for(variant)
    bbox_geom = from_shape(box(min_lon, min_lat, max_lon, max_lat), srid=4326)
    centroid_geom = from_shape(Point(*cfg.centroid), srid=4326)

    with session_scope() as session:
        city = session.scalar(select(City).where(City.slug == cfg.slug))
        if city is None:
            city = City(
                slug=cfg.slug, name=cfg.name, bbox=bbox_geom, centroid=centroid_geom
            )
            session.add(city)
            session.flush()
            log.info("city_created", slug=cfg.slug, city_id=city.id, variant=variant or "full")
        else:
            city.name = cfg.name
            city.bbox = bbox_geom
            city.centroid = centroid_geom
            log.info("city_updated", slug=cfg.slug, city_id=city.id, variant=variant or "full")
        return city.id
