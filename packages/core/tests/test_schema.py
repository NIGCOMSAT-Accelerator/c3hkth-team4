"""P1 acceptance: the PostGIS schema round-trips geometry with SRID intact.

Every test runs inside a transaction that is rolled back, so the suite leaves
no residue in a database the ingestion pipelines also write to.
"""

from __future__ import annotations

import datetime as dt

import pytest
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import LineString, Point, box
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import get_city
from core.db import engine
from core.models import City, IngestionRun, RoadSegment, SegmentRisk


@pytest.fixture()
def session():
    connection = engine.connect()
    trans = connection.begin()
    s = Session(bind=connection)
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        connection.close()


# A slug of its own: the real "abuja" row exists as soon as the ingestion
# pipeline has run, and cities.slug is UNIQUE. Geometry still comes from
# cities.yaml, so this remains a test of the real configuration.
TEST_SLUG = "abuja-pytest"


@pytest.fixture()
def abuja(session: Session) -> City:
    """Insert Abuja straight from cities.yaml — config and schema agree or fail."""
    cfg = get_city("abuja")
    min_lon, min_lat, max_lon, max_lat = cfg.bbox
    city = City(
        slug=TEST_SLUG,
        name=cfg.name,
        bbox=from_shape(box(min_lon, min_lat, max_lon, max_lat), srid=4326),
        centroid=from_shape(Point(*cfg.centroid), srid=4326),
    )
    session.add(city)
    session.flush()
    return city


def test_city_roundtrips_with_srid_4326(session: Session, abuja: City):
    """The P1 acceptance criterion, stated directly."""
    stored = session.get(City, abuja.id)
    assert stored is not None
    assert stored.slug == TEST_SLUG

    bbox_srid, centroid_srid = session.execute(
        select(func.ST_SRID(City.bbox), func.ST_SRID(City.centroid)).where(
            City.id == abuja.id
        )
    ).one()
    assert bbox_srid == 4326, "bbox lost its SRID on the way into PostGIS"
    assert centroid_srid == 4326, "centroid lost its SRID"

    # Geometry survives the round trip, not just the SRID metadata.
    cfg = get_city("abuja")
    assert to_shape(stored.bbox).bounds == pytest.approx(tuple(cfg.bbox))
    assert to_shape(stored.centroid).coords[0] == pytest.approx(tuple(cfg.centroid))


def test_centroid_falls_inside_bbox(session: Session, abuja: City):
    """A wrong lon/lat ordering in cities.yaml would silently poison every AOI."""
    contained = session.scalar(
        select(func.ST_Contains(City.bbox, City.centroid)).where(City.id == abuja.id)
    )
    assert contained is True


def test_road_segment_stores_linestring_and_cascades(session: Session, abuja: City):
    seg = RoadSegment(
        city_id=abuja.id,
        osm_way_id=12345,
        u_node=1,
        v_node=2,
        name="Airport Road",
        highway_class="trunk",
        length_m=118.0,
        geom=from_shape(LineString([(7.4913, 9.0579), (7.4920, 9.0585)]), srid=4326),
    )
    session.add(seg)
    session.flush()

    srid = session.scalar(select(func.ST_SRID(RoadSegment.geom)).where(RoadSegment.id == seg.id))
    assert srid == 4326

    # Length computed in EPSG:32632, the metric CRS declared in cities.yaml.
    metres = session.scalar(
        select(
            func.ST_Length(
                func.ST_Transform(RoadSegment.geom, get_city("abuja").utm_epsg)
            )
        ).where(RoadSegment.id == seg.id)
    )
    assert 50 < metres < 200, f"implausible segment length {metres}m"

    # Deleting a city must take its segments with it, or reloads leave orphans.
    # Scoped to this city: the real Abuja network lives in the same table.
    city_id = abuja.id
    session.delete(abuja)
    session.flush()
    remaining = session.scalar(
        select(func.count()).select_from(RoadSegment).where(RoadSegment.city_id == city_id)
    )
    assert remaining == 0, "city delete left orphaned road segments"


def test_segment_risk_is_keyed_by_segment_and_date(session: Session, abuja: City):
    seg = RoadSegment(
        city_id=abuja.id,
        geom=from_shape(LineString([(7.49, 9.05), (7.50, 9.06)]), srid=4326),
    )
    session.add(seg)
    session.flush()

    today = dt.date(2026, 8, 8)
    session.add(
        SegmentRisk(
            segment_id=seg.id,
            valid_date=today,
            risk_score=72.5,
            risk_band="High",
            rain_7d_mm=88.0,
            rain_24h_forecast_mm=31.0,
            contributions={"base": 0.61, "wetness": 0.73, "trigger": 0.62},
        )
    )
    session.flush()

    row = session.get(SegmentRisk, (seg.id, today))
    assert row is not None
    assert row.risk_band == "High"
    # JSONB must survive as a dict — the "Why" panel reads it directly.
    assert row.contributions["base"] == pytest.approx(0.61)


def test_ingestion_run_records_a_pipeline_stage(session: Session, abuja: City):
    """Every pipeline stage writes one of these. It is a demo asset."""
    run = IngestionRun(source="osm_roads", city_id=abuja.id, status="running")
    session.add(run)
    session.flush()
    assert run.started_at is not None

    run.status = "success"
    run.records = 41230
    run.finished_at = dt.datetime.now(dt.UTC)
    run.notes = "cached graphml"
    session.flush()

    stored = session.get(IngestionRun, run.id)
    assert stored.status == "success"
    assert stored.records == 41230
