"""P2 — OpenStreetMap drivable network for a city, split into ~100m segments.

The graphml is cached to data/cache/osm/ and reloaded from disk when present.
This is not an optimisation: the demo must run with the network off, and the
same cached graph is what the P7 routing engine loads at API startup.

  python -m processing.ingest.roads --city abuja
  python -m processing.ingest.roads --city abuja --stats
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

import geopandas as gpd
import osmnx as ox
import pandas as pd
from geoalchemy2.shape import from_shape
from shapely.geometry import LineString, MultiLineString
from shapely.geometry import box as shp_box
from shapely.ops import substring
from sqlalchemy import delete, func, insert, select

from core.config import get_city, settings
from core.db import session_scope
from core.logging import configure_logging, get_logger
from core.models import City, RoadSegment
from processing.common import ensure_city, ingestion_run

configure_logging("processing")
log = get_logger("roads")

MAX_SEGMENT_LEN_M = 120.0
MIN_SEGMENT_LEN_M = 30.0
# Scope guard from the P2 brief. Above this we tighten the AOI rather than
# blow the schedule — cities.yaml already carries a `municipal` variant.
DEFAULT_MAX_SEGMENTS = 80_000

# The routable arterial network: what a state emergency agency or a logistics
# platform actually plans on. Measured on the full FCT graph, residential
# streets are 187,826 of 233,371 edges — 80% of the data — and including them
# puts even the municipal AOI at 212k segments, far past the budget. Excluding
# them brings the municipal AOI to ~43k, which fits comfortably.
#
# osmnx network_type="drive" already excludes service roads, tracks and
# footpaths, so there is nothing further to filter there.
ARTERIAL_CLASSES = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
    }
)


def _first(value: Any) -> Any:
    """OSM tags are sometimes lists (a way carrying two names, two classes)."""
    if isinstance(value, (list, tuple, set)):
        return next(iter(value), None)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def graph_path(slug: str, variant: str | None) -> Path:
    suffix = f"_{variant}" if variant else ""
    return settings.cache_dir / "osm" / f"{slug}{suffix}.graphml"


def load_graph(slug: str, bbox: tuple[float, float, float, float], variant: str | None):
    """Load the drivable graph from cache, downloading only on a cache miss."""
    path = graph_path(slug, variant)
    if path.exists():
        log.info("graph_cache_hit", path=str(path))
        return ox.load_graphml(path)

    # A wider cached graph already contains every edge a narrower AOI needs,
    # and build_segments clips by bbox anyway. Downloading again would cost
    # ~6 minutes on this connection for data we already hold.
    full_path = graph_path(slug, None)
    if variant and full_path.exists():
        log.info("graph_reuse_wider_cache", path=str(full_path), variant=variant)
        return ox.load_graphml(full_path)

    if settings.demo_mode:
        raise RuntimeError(
            f"DEMO_MODE is on and {path} is missing. The demo never downloads; "
            "run the ingestion once with DEMO_MODE=false to populate the cache."
        )

    min_lon, min_lat, max_lon, max_lat = bbox
    log.info("graph_download_start", bbox=bbox, note="this takes a few minutes")

    # osmnx 2.x takes bbox=(left, bottom, right, top); 1.x took north/south/east/west.
    try:
        graph = ox.graph_from_bbox(
            bbox=(min_lon, min_lat, max_lon, max_lat), network_type="drive"
        )
    except TypeError:
        graph = ox.graph_from_bbox(
            north=max_lat, south=min_lat, east=max_lon, west=min_lon, network_type="drive"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(graph, path)
    log.info("graph_cached", path=str(path), nodes=len(graph), edges=graph.number_of_edges())
    return graph


def split_linestring(
    geom: LineString, max_len: float = MAX_SEGMENT_LEN_M, min_len: float = MIN_SEGMENT_LEN_M
) -> list[LineString]:
    """Cut a metric-CRS line into pieces no longer than max_len.

    Equal division rather than fixed-length chunks with a remainder: with
    n = ceil(length / max_len) equal pieces, every piece lands in
    [max_len/2, max_len], so a sub-min_len stub can never be produced and the
    "merge the runt into the previous piece" rule has nothing to do. Whole
    edges already shorter than min_len are kept intact — they are real roads,
    and dropping them would punch holes in the routing graph.
    """
    length = geom.length
    if length <= max_len:
        return [geom]

    n = math.ceil(length / max_len)
    piece_len = length / n
    pieces: list[LineString] = []
    for i in range(n):
        part = substring(geom, i * piece_len, (i + 1) * piece_len)
        if isinstance(part, LineString) and not part.is_empty and part.length > 0:
            pieces.append(part)
    return pieces or [geom]


def build_segments(
    graph,
    utm_epsg: int,
    bbox: tuple[float, float, float, float] | None = None,
    include_classes: frozenset[str] | None = ARTERIAL_CLASSES,
) -> gpd.GeoDataFrame:
    """Explode the edge set into ~100m segments, geometry returned as EPSG:4326.

    Clips to `bbox` (so a graph cached for a wider AOI can serve a narrower
    one) and keeps only `include_classes`. Pass include_classes=None to keep
    every drivable class.
    """
    edges = ox.graph_to_gdfs(graph, nodes=False, edges=True).reset_index()
    log.info("edges_loaded", edges=len(edges))

    if bbox is not None:
        before = len(edges)
        edges = edges[edges.intersects(shp_box(*bbox))].copy()
        log.info("edges_clipped_to_bbox", before=before, after=len(edges), bbox=bbox)

    if include_classes is not None:
        before = len(edges)
        keep = edges["highway"].map(lambda v: _first(v) in include_classes)
        edges = edges[keep].copy()
        log.info(
            "edges_filtered_by_class",
            before=before,
            after=len(edges),
            excluded="residential and other non-arterial classes",
        )

    if edges.empty:
        raise SystemExit("No edges survived the bbox/class filter — check the AOI.")

    # All length maths happens in the projected CRS declared in cities.yaml.
    edges_utm = edges.to_crs(epsg=utm_epsg)

    rows: list[dict[str, Any]] = []
    geoms: list[LineString] = []
    for row in edges_utm.itertuples(index=False):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        # A handful of OSM edges come back as MultiLineString.
        parts = list(geom.geoms) if isinstance(geom, MultiLineString) else [geom]
        for part in parts:
            if not isinstance(part, LineString):
                continue
            for piece in split_linestring(part):
                # Children inherit the parent way's identity.
                rows.append(
                    {
                        "osm_way_id": _first(getattr(row, "osmid", None)),
                        "u_node": getattr(row, "u", None),
                        "v_node": getattr(row, "v", None),
                        "name": _first(getattr(row, "name", None)),
                        "highway_class": _first(getattr(row, "highway", None)),
                        "length_m": round(piece.length, 2),
                    }
                )
                geoms.append(piece)

    segments = gpd.GeoDataFrame(rows, geometry=geoms, crs=f"EPSG:{utm_epsg}")
    # Store as 4326 — hard rule 4.
    segments = segments.to_crs(epsg=4326)
    log.info("segments_built", segments=len(segments))
    return segments


def load_to_db(segments: gpd.GeoDataFrame, city_id: int, handle) -> int:
    """Replace this city's segments in one transaction. Re-running is safe."""
    records = []
    for row, geom in zip(
        segments.drop(columns="geometry").to_dict("records"), segments.geometry, strict=True
    ):
        row = {k: (None if pd.isna(v) else v) for k, v in row.items()}
        row["city_id"] = city_id
        row["geom"] = from_shape(geom, srid=4326)
        records.append(row)

    with session_scope() as session:
        deleted = session.execute(
            delete(RoadSegment).where(RoadSegment.city_id == city_id)
        ).rowcount
        if deleted:
            log.info("existing_segments_cleared", deleted=deleted)
        # Chunked so a 40k-row statement does not balloon memory.
        for start in range(0, len(records), 5_000):
            session.execute(insert(RoadSegment), records[start : start + 5_000])

    handle.records = len(records)
    return len(records)


def print_stats(slug: str) -> int:
    """--stats: segment count, total km, breakdown by highway class."""
    with session_scope() as session:
        city_id = session.scalar(select(City.id).where(City.slug == slug))
        if city_id is None:
            print(f"City {slug!r} is not loaded. Run without --stats first.")
            return 1

        total, km = session.execute(
            select(func.count(), func.coalesce(func.sum(RoadSegment.length_m), 0) / 1000.0).where(
                RoadSegment.city_id == city_id
            )
        ).one()

        print(f"\n  {slug}: {total:,} segments, {km:,.1f} km total\n")
        if not total:
            return 1

        print(f"  {'highway_class':<18}{'segments':>10}{'km':>12}")
        print(f"  {'-' * 40}")
        for cls, n, class_km in session.execute(
            select(
                func.coalesce(RoadSegment.highway_class, "(none)"),
                func.count(),
                func.sum(RoadSegment.length_m) / 1000.0,
            )
            .where(RoadSegment.city_id == city_id)
            .group_by(RoadSegment.highway_class)
            .order_by(func.count().desc())
        ):
            print(f"  {cls:<18}{n:>10,}{class_km:>12,.1f}")

        mean_len = session.scalar(
            select(func.avg(RoadSegment.length_m)).where(RoadSegment.city_id == city_id)
        )
        print(f"\n  mean segment length: {mean_len:.1f} m")
        named = session.scalar(
            select(func.count()).where(
                RoadSegment.city_id == city_id, RoadSegment.name.isnot(None)
            )
        )
        print(f"  named segments:      {named:,} ({100 * named / total:.0f}%)\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest the OSM drivable network.")
    parser.add_argument("--city", default="abuja")
    parser.add_argument(
        "--variant",
        default=None,
        help="AOI variant from cities.yaml (e.g. 'municipal' to tighten the bbox).",
    )
    parser.add_argument("--stats", action="store_true", help="Report on what is loaded.")
    parser.add_argument("--max-segments", type=int, default=DEFAULT_MAX_SEGMENTS)
    parser.add_argument(
        "--include-residential",
        action="store_true",
        help="Keep residential streets. They are 80%% of the network and push "
        "even the municipal AOI to ~212k segments — raise --max-segments too.",
    )
    args = parser.parse_args(argv)

    if args.stats:
        return print_stats(args.city)

    cfg = get_city(args.city)
    settings.ensure_dirs()
    city_id = ensure_city(cfg, args.variant)

    bbox = cfg.bbox_for(args.variant)
    include = None if args.include_residential else ARTERIAL_CLASSES

    with ingestion_run("osm_roads", city_id) as handle:
        graph = load_graph(cfg.slug, bbox, args.variant)
        segments = build_segments(graph, cfg.utm_epsg, bbox=bbox, include_classes=include)

        # Scope guard: stop loudly rather than silently spending the schedule.
        if len(segments) > args.max_segments:
            raise SystemExit(
                f"\nSCOPE GUARD TRIPPED\n"
                f"  {len(segments):,} segments exceeds the {args.max_segments:,} budget.\n"
                f"  Nothing was written to the database.\n\n"
                f"  Re-run against the tighter AOI already defined in cities.yaml:\n"
                f"    python -m processing.ingest.roads --city {cfg.slug} --variant municipal\n"
            )

        count = load_to_db(segments, city_id, handle)
        handle.note(f"variant={args.variant or 'full'}")

    log.info("roads_loaded", city=cfg.slug, segments=count)
    return print_stats(cfg.slug)


if __name__ == "__main__":
    sys.exit(main())
