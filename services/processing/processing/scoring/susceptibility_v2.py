"""P5 stage B — flood susceptibility per road segment, from satellite evidence.

This replaces susceptibility_v1's placeholder values in the same column, so
nothing downstream needs to know which stage produced the number.

  python -m processing.scoring.susceptibility_v2 --city abuja
  python -m processing.scoring.susceptibility_v2 --report

WHY THESE FEATURES AND THESE WEIGHTS
------------------------------------
Every term is percentile-ranked across the city before weighting, so the index
is a relative ordering of *this* road network rather than an absolute depth
prediction we cannot honestly make. The weights are physically motivated, not
tuned to a target, and P6 tests them against satellite-observed inundation.

  hand_term = 1 - pctile(hand_min)                      weight 0.40
      Height Above Nearest Drainage is the strongest single predictor of
      fluvial flood exposure. It answers the question that actually decides
      whether a road floods: how far above the nearest channel is it? A road
      2 m above a stream is exposed; the same road 40 m above it is not,
      however hard it rains. We take the segment MINIMUM rather than the mean
      because a road is impassable at its lowest point — averaging a dip out
      of existence is precisely the error that gets drivers into water.

  wofs_term = pctile(wofs_freq_max)                     weight 0.25
      Water Observations from Space: how often each pixel was seen as water
      across ~40 years of Landsat. This is the floodplain prior, and it is
      pure observation — no model, no interpolation. Ground that has been wet
      repeatedly will be wet again. MAXIMUM over the segment, for the same
      reason as HAND's minimum: the worst pixel governs passability.

  slope_term = 1 - pctile(slope_mean)                   weight 0.15
      Flat ground sheds water slowly and ponds. Steep ground drains. Mean is
      right here because drainage is a property of the segment as a whole.

  drainage_term = 1 - pctile(dist_to_drainage_m)        weight 0.15
      Proximity to a channel. Distinct from HAND: a road can sit close to a
      channel but well above it, or far from any channel in a flat basin.
      The two together describe position far better than either alone.

  crossing_term = 1.0 if crosses_drainage else 0.0      weight 0.05
      Where a road crosses a channel there is a culvert or a low-water
      crossing, and that is where roads actually fail — the structure blocks
      with debris and overtops. Small weight because it is a coarse binary
      flag, not because the effect is small.

LIMITATIONS, stated here because they belong in MODEL.md
    No drainage-infrastructure data: we cannot see culvert capacity, blockage
    or pumping. No ground truth against reported road-flooding incidents.
    HAND assumes fluvial flooding and under-weights pluvial (surface-water)
    flooding in built-up areas with poor drainage. The index is a relative
    ranking, not a probability.
"""

from __future__ import annotations

import argparse
import io
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterstats import zonal_stats
from sqlalchemy import select, text

from core.config import get_city, settings
from core.db import session_scope
from core.logging import configure_logging, get_logger
from core.models import City
from processing.common import ingestion_run

configure_logging("processing")
log = get_logger("susceptibility_v2")

# One config dict. MODEL.md and the /v1/meta/model endpoint both read this.
WEIGHTS: dict[str, float] = {
    "hand": 0.40,
    "wofs": 0.25,
    "slope": 0.15,
    "drainage": 0.15,
    "crossing": 0.05,
}

SEGMENT_BUFFER_M = 15.0  # roads are ~10-20 m wide; sample the carriageway, not the field
NODATA = -9999.0


def load_segments(city_id: int, utm_epsg: int) -> gpd.GeoDataFrame:
    sql = text(
        "SELECT id, name, highway_class, ST_AsBinary(geom) AS wkb "
        "FROM road_segments WHERE city_id = :cid"
    )
    with session_scope() as session:
        rows = session.execute(sql, {"cid": city_id}).fetchall()

    frame = pd.DataFrame(rows, columns=["id", "name", "highway_class", "wkb"])
    gdf = gpd.GeoDataFrame(
        frame.drop(columns="wkb"),
        geometry=gpd.GeoSeries.from_wkb(frame["wkb"]),
        crs="EPSG:4326",
    ).to_crs(epsg=utm_epsg)
    log.info("segments_loaded", segments=len(gdf), crs=f"EPSG:{utm_epsg}")
    return gdf


def attach_raster_features(gdf: gpd.GeoDataFrame, derived) -> gpd.GeoDataFrame:
    """Zonal statistics over a 15 m buffer of each segment.

    All five rasters share one grid (asserted in P3/P4), so each is read once
    into memory and reused across 42k polygons rather than reopened per zone.
    """
    buffers = gdf.geometry.buffer(SEGMENT_BUFFER_M)

    wanted = [
        ("dem.tif", "elev_mean", "mean"),
        ("slope.tif", "slope_mean", "mean"),
        ("hand.tif", "hand_min", "min"),
        ("hand.tif", "hand_mean", "mean"),
        ("wofs_freq.tif", "wofs_freq_max", "max"),
    ]

    cache: dict[str, tuple[np.ndarray, object]] = {}
    for filename, column, stat in wanted:
        if filename not in cache:
            with rasterio.open(derived / filename) as src:
                array = src.read(1).astype("float32")
                # rasterstats handles a sentinel more predictably than NaN.
                array = np.where(np.isfinite(array), array, NODATA)
                cache[filename] = (array, src.transform)
        array, transform = cache[filename]

        stats = zonal_stats(
            buffers,
            array,
            affine=transform,
            stats=[stat],
            nodata=NODATA,
            all_touched=True,
        )
        gdf[column] = [s[stat] for s in stats]
        missing = gdf[column].isna().sum()
        log.info("zonal_stats_done", column=column, source=filename, missing=int(missing))

    return gdf


def attach_drainage_features(gdf: gpd.GeoDataFrame, derived, utm_epsg: int) -> gpd.GeoDataFrame:
    """Distance from the segment centroid to the nearest channel, and crossings."""
    drainage = gpd.read_file(derived / "drainage.gpkg").to_crs(epsg=utm_epsg)
    log.info("drainage_loaded", features=len(drainage))

    centroids = gpd.GeoDataFrame(
        {"id": gdf["id"]}, geometry=gdf.geometry.centroid, crs=gdf.crs
    )
    nearest = gpd.sjoin_nearest(
        centroids, drainage[["geometry"]], how="left", distance_col="dist_to_drainage_m"
    )
    # sjoin_nearest emits one row per tied neighbour; keep the first per segment.
    nearest = nearest.groupby("id", as_index=False)["dist_to_drainage_m"].min()
    gdf = gdf.merge(nearest, on="id", how="left")

    crossing_ids = set(
        gpd.sjoin(
            gdf[["id", "geometry"]], drainage[["geometry"]], how="inner", predicate="intersects"
        )["id"]
    )
    gdf["crosses_drainage"] = gdf["id"].isin(crossing_ids)

    log.info(
        "drainage_features_attached",
        crossings=int(gdf["crosses_drainage"].sum()),
        median_dist_m=round(float(gdf["dist_to_drainage_m"].median()), 1),
    )
    return gdf


def score(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Percentile-rank each term across the city, then weight and combine."""
    feature_columns = [
        "elev_mean",
        "slope_mean",
        "hand_min",
        "hand_mean",
        "wofs_freq_max",
        "dist_to_drainage_m",
    ]
    # Never leave NULLs: a segment the rasters could not cover takes the city
    # median, which is neutral in a percentile ranking. NULLs crash the router.
    for column in feature_columns:
        filled = gdf[column].isna().sum()
        if filled:
            gdf[column] = gdf[column].fillna(gdf[column].median())
            log.info("feature_gap_filled", column=column, segments=int(filled))

    pct = {c: gdf[c].rank(pct=True) for c in feature_columns}

    terms = {
        "hand": 1.0 - pct["hand_min"],
        "wofs": pct["wofs_freq_max"],
        "slope": 1.0 - pct["slope_mean"],
        "drainage": 1.0 - pct["dist_to_drainage_m"],
        "crossing": gdf["crosses_drainage"].astype(float),
    }

    susceptibility = sum(WEIGHTS[name] * term for name, term in terms.items())
    gdf["susceptibility"] = susceptibility.clip(0.0, 1.0)
    gdf["susceptibility_pctile"] = gdf["susceptibility"].rank(pct=True) * 100.0

    for name, term in terms.items():
        log.info("term_summary", term=name, weight=WEIGHTS[name], mean=round(float(term.mean()), 3))
    return gdf


def bulk_update(gdf: gpd.GeoDataFrame) -> int:
    """COPY into a temp table, then one UPDATE ... FROM. Never row-by-row."""
    columns = [
        "id",
        "elev_mean",
        "slope_mean",
        "hand_min",
        "hand_mean",
        "wofs_freq_max",
        "dist_to_drainage_m",
        "crosses_drainage",
        "susceptibility",
        "susceptibility_pctile",
    ]
    buffer = io.StringIO()
    gdf[columns].to_csv(buffer, index=False, header=False, na_rep="\\N")
    buffer.seek(0)

    with session_scope() as session:
        connection = session.connection().connection
        with connection.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE seg_scores ("
                " id bigint PRIMARY KEY, elev_mean float, slope_mean float,"
                " hand_min float, hand_mean float, wofs_freq_max float,"
                " dist_to_drainage_m float, crosses_drainage boolean,"
                " susceptibility float, susceptibility_pctile float"
                ") ON COMMIT DROP"
            )
            with cur.copy("COPY seg_scores FROM STDIN WITH (FORMAT csv, NULL '\\N')") as copy:
                copy.write(buffer.read())
            cur.execute(
                "UPDATE road_segments rs SET"
                " elev_mean = s.elev_mean, slope_mean = s.slope_mean,"
                " hand_min = s.hand_min, hand_mean = s.hand_mean,"
                " wofs_freq_max = s.wofs_freq_max,"
                " dist_to_drainage_m = s.dist_to_drainage_m,"
                " crosses_drainage = s.crosses_drainage,"
                " susceptibility = s.susceptibility,"
                " susceptibility_pctile = s.susceptibility_pctile,"
                " features_updated_at = now()"
                " FROM seg_scores s WHERE rs.id = s.id"
            )
            updated = cur.rowcount
    return updated


def report(city_slug: str) -> int:
    """Top 10 named roads by susceptibility — the human sanity check."""
    with session_scope() as session:
        city_id = session.scalar(select(City.id).where(City.slug == city_slug))
        rows = session.execute(
            text(
                "SELECT name, highway_class, round(avg(susceptibility)::numeric,3) AS s,"
                "  round(avg(hand_min)::numeric,1) AS hand,"
                "  round(avg(wofs_freq_max)::numeric,3) AS wofs,"
                "  round(avg(dist_to_drainage_m)::numeric,0) AS dist,"
                "  bool_or(crosses_drainage) AS crosses, count(*) AS n"
                " FROM road_segments WHERE city_id = :cid AND name IS NOT NULL"
                " GROUP BY name, highway_class HAVING count(*) >= 3"
                " ORDER BY avg(susceptibility) DESC LIMIT 10"
            ),
            {"cid": city_id},
        ).fetchall()

    print("\n  Top 10 most flood-susceptible named roads\n")
    print(f"  {'road':<34}{'class':<13}{'susc':>7}{'HAND':>8}{'WOfS':>7}{'drain':>8}{'segs':>6}")
    print(f"  {'-' * 83}")
    for name, cls, susc, hand, wofs, dist, crosses, n in rows:
        flag = "*" if crosses else " "
        print(
            f"  {name[:33]:<34}{(cls or '')[:12]:<13}{susc:>7}{hand:>8}{wofs:>7}{dist:>7}m{n:>6}{flag}"
        )
    print("\n  * crosses a mapped drainage channel (culvert / low-water crossing)\n")
    return 0


def run(city_slug: str) -> int:
    cfg = get_city(city_slug)
    derived = settings.city_derived_dir(cfg.slug)
    for required in ("dem.tif", "slope.tif", "hand.tif", "wofs_freq.tif", "drainage.gpkg"):
        if not (derived / required).exists():
            raise SystemExit(f"{derived / required} missing. Run terrain and wofs first.")

    with session_scope() as session:
        city_id = session.scalar(select(City.id).where(City.slug == cfg.slug))
    if city_id is None:
        raise SystemExit(f"City {cfg.slug!r} not loaded. Run processing.ingest.roads first.")

    with ingestion_run("susceptibility_v2", city_id) as handle:
        gdf = load_segments(city_id, cfg.utm_epsg)
        gdf = attach_raster_features(gdf, derived)
        gdf = attach_drainage_features(gdf, derived, cfg.utm_epsg)
        gdf = score(gdf)
        updated = bulk_update(gdf)
        handle.records = updated
        handle.note(f"weights={WEIGHTS}")

    with session_scope() as session:
        nulls = session.scalar(
            text(
                "SELECT count(*) FROM road_segments WHERE city_id = :cid"
                " AND (susceptibility IS NULL OR susceptibility_pctile IS NULL)"
            ),
            {"cid": city_id},
        )
    if nulls:
        raise SystemExit(f"{nulls} segments still NULL — the router would crash.")

    print(f"\n  segments scored : {updated:,}")
    print(f"  NULLs           : 0")
    print(f"  crossings       : {int(gdf['crosses_drainage'].sum()):,}")
    print(f"  susceptibility  : min {gdf['susceptibility'].min():.3f}  "
          f"median {gdf['susceptibility'].median():.3f}  max {gdf['susceptibility'].max():.3f}")
    return report(cfg.slug)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="abuja")
    parser.add_argument("--report", action="store_true", help="Only print the top 10.")
    args = parser.parse_args(argv)
    return report(args.city) if args.report else run(args.city)


if __name__ == "__main__":
    sys.exit(main())
