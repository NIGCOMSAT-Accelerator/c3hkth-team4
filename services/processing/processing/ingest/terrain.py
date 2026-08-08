"""P3 — elevation, slope and HAND (Height Above Nearest Drainage) for a city.

HAND is the strongest single flood predictor we have and carries the largest
weight (0.40) in the v2 susceptibility index. It answers the question that
actually matters for a road: how far above the nearest channel is this point?
A road 2 m above a stream floods; a road 40 m above the same stream does not,
however much rain falls.

  python -m processing.ingest.terrain --city abuja --variant municipal

Two decisions worth knowing before editing this file:

1. The DEM tiles are Cloud Optimized GeoTIFFs, so we range-request only the
   window covering our AOI (~12% of a tile) rather than pulling 87 MB of full
   tiles. Bandwidth is the binding constraint on this project. The clipped
   mosaic is cached, so every later run is offline.

2. Hydrology runs on a BUFFERED DEM, and only the outputs are clipped to the
   AOI. Flow routing on a bbox-clipped DEM silently truncates catchments —
   water entering from outside is never routed — so HAND comes out wrong near
   every edge, which is exactly where the arterial roads leave town.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.features import shapes as rio_shapes
from rasterio.merge import merge as rio_merge
from rasterio.warp import Resampling, calculate_default_transform, reproject
from rasterio.windows import Window, from_bounds
from shapely.geometry import shape as shapely_shape

from core.config import get_city, settings
from core.logging import configure_logging, get_logger
from processing.common import ensure_city, ingestion_run

configure_logging("processing")
log = get_logger("terrain")

# Copernicus DEM GLO-30, AWS open data, public and unauthenticated.
DEM_BUCKET = "copernicus-dem-30m"
DEM_REGION = "eu-central-1"
DEM_RES_DEG = 1.0 / 3600.0  # GLO-30 native: 1 arc-second

TARGET_RES_M = 30.0
DEFAULT_BUFFER_DEG = 0.1  # ~11 km of upstream context for flow routing
DEFAULT_ACCUM_THRESHOLD = 500  # cells; at 30 m that is ~0.45 km² contributing area

# Above this many cells we drop the hydrology to 60 m rather than miss the
# deadline. At the municipal AOI we are ~2.6M cells, so this will not fire.
MAX_HYDRO_CELLS = 25_000_000

# If the AOI's median HAND falls outside this band, flow routing has failed and
# everything downstream is garbage. Fail loudly rather than ship silent noise.
HAND_MEDIAN_MIN_M = 5.0
HAND_MEDIAN_MAX_M = 60.0


# --------------------------------------------------------------------------
# Tile discovery
# --------------------------------------------------------------------------


def tiles_for_bbox(bbox: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    """Degree tiles (lat, lon) whose 1x1 cell intersects bbox."""
    min_lon, min_lat, max_lon, max_lat = bbox
    lats = range(math.floor(min_lat), math.floor(max_lat) + 1)
    lons = range(math.floor(min_lon), math.floor(max_lon) + 1)
    return [(la, lo) for la in lats for lo in lons]


def tile_prefix(lat: int, lon: int) -> str:
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM/"


def discover_dem_urls(bbox: tuple[float, float, float, float]) -> list[str]:
    """List the bucket and return the DEM asset URL for each intersecting tile.

    Tile names are never hardcoded — we list the prefix and take what exists,
    because a guessed name that 404s at 2 AM is not a debuggable failure.
    """
    import boto3
    from botocore import UNSIGNED
    from botocore.client import Config

    s3 = boto3.client(
        "s3", region_name=DEM_REGION, config=Config(signature_version=UNSIGNED)
    )

    urls: list[str] = []
    for lat, lon in tiles_for_bbox(bbox):
        prefix = tile_prefix(lat, lon)
        response = s3.list_objects_v2(Bucket=DEM_BUCKET, Prefix=prefix)
        contents = response.get("Contents", [])
        # The elevation asset, not the HEM/EDM/FLM/WBM siblings or PREVIEW/.
        assets = [
            obj
            for obj in contents
            if obj["Key"].endswith("_DEM.tif") and "PREVIEW" not in obj["Key"]
        ]
        if not assets:
            log.warning("dem_tile_missing", prefix=prefix, found=len(contents))
            continue
        key = assets[0]["Key"]
        size_mb = assets[0]["Size"] / 1e6
        log.info("dem_tile_found", key=key.split("/")[-1], size_mb=round(size_mb, 1))
        urls.append(f"/vsicurl/https://{DEM_BUCKET}.s3.amazonaws.com/{key}")

    if not urls:
        raise RuntimeError(
            f"No Copernicus DEM tiles found for bbox {bbox}. "
            f"Checked prefixes: {[tile_prefix(*t) for t in tiles_for_bbox(bbox)]}"
        )
    return urls


# --------------------------------------------------------------------------
# Raster helpers
# --------------------------------------------------------------------------


def write_raster(
    path: Path,
    array: np.ndarray,
    transform,
    crs,
    nodata: float | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype=array.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="deflate",
        tiled=True,
    ) as dst:
        dst.write(array, 1)


def fetch_buffered_dem(
    bbox_buffered: tuple[float, float, float, float], cache_path: Path, force: bool
) -> tuple[np.ndarray, object]:
    """Windowed COG read of the buffered AOI, cached to disk in EPSG:4326."""
    if cache_path.exists() and not force:
        log.info("dem_cache_hit", path=str(cache_path))
        with rasterio.open(cache_path) as src:
            return src.read(1), src.transform

    if settings.demo_mode:
        raise RuntimeError(
            f"DEMO_MODE is on and {cache_path} is missing. The demo never "
            "downloads; run once with DEMO_MODE=false to populate the cache."
        )

    urls = discover_dem_urls(bbox_buffered)
    log.info("dem_windowed_read_start", tiles=len(urls), bbox=bbox_buffered)
    started = time.time()

    # merge(bounds=...) reads only the intersecting window from each source,
    # so over /vsicurl this is a range request, not a tile download.
    sources = [rasterio.open(u) for u in urls]
    try:
        mosaic, transform = rio_merge(sources, bounds=bbox_buffered, res=DEM_RES_DEG)
    finally:
        for src in sources:
            src.close()

    array = mosaic[0].astype("float32")
    log.info(
        "dem_windowed_read_done",
        shape=list(array.shape),
        seconds=round(time.time() - started, 1),
        megabytes_in_memory=round(array.nbytes / 1e6, 1),
    )

    write_raster(cache_path, array, transform, CRS.from_epsg(4326), nodata=None)
    log.info("dem_cached", path=str(cache_path))
    return array, transform


def reproject_to_metric(
    array: np.ndarray, transform, utm_epsg: int
) -> tuple[np.ndarray, object]:
    """Reproject the geographic DEM to EPSG:32632 at 30 m."""
    src_crs = CRS.from_epsg(4326)
    dst_crs = CRS.from_epsg(utm_epsg)
    height, width = array.shape
    left, top = transform * (0, 0)
    right, bottom = transform * (width, height)

    dst_transform, dst_w, dst_h = calculate_default_transform(
        src_crs, dst_crs, width, height, left, bottom, right, top, resolution=TARGET_RES_M
    )
    destination = np.empty((dst_h, dst_w), dtype="float32")
    reproject(
        source=array,
        destination=destination,
        src_transform=transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=Resampling.bilinear,
    )
    log.info(
        "dem_reprojected", crs=f"EPSG:{utm_epsg}", shape=[dst_h, dst_w], res_m=TARGET_RES_M
    )
    return destination, dst_transform


def aoi_window(transform, shape: tuple[int, int], aoi_bounds_metric) -> Window:
    """The window of the buffered grid covering the AOI.

    Computed once and reused for dem/slope/hand so all three are guaranteed to
    share a shape and transform — hard rule 3.
    """
    window = from_bounds(*aoi_bounds_metric, transform=transform).round_offsets().round_lengths()
    # Clamp to the array, in case the buffer got clipped at a tile edge.
    col_off = max(0, int(window.col_off))
    row_off = max(0, int(window.row_off))
    width = min(int(window.width), shape[1] - col_off)
    height = min(int(window.height), shape[0] - row_off)
    return Window(col_off, row_off, width, height)


def clip(array: np.ndarray, window: Window) -> np.ndarray:
    return array[
        window.row_off : window.row_off + window.height,
        window.col_off : window.col_off + window.width,
    ]


def compute_slope(dem: np.ndarray, cell_size: float) -> np.ndarray:
    """Slope in degrees. Computed on the buffered DEM so edges have neighbours."""
    dz_dy, dz_dx = np.gradient(dem, cell_size)
    return np.degrees(np.arctan(np.hypot(dz_dx, dz_dy))).astype("float32")


# --------------------------------------------------------------------------
# Hydrology
# --------------------------------------------------------------------------


def compute_hand(
    dem_path: Path, accum_threshold: int
) -> tuple[np.ndarray, np.ndarray, float]:
    """Condition the DEM, route flow, and compute HAND.

    Returns (hand, drainage_mask, drainage_fraction).
    """
    # Must precede the pysheds import — see processing.compat.
    from processing.compat import patch_numpy_for_pysheds

    patch_numpy_for_pysheds()

    from pysheds.grid import Grid

    grid = Grid.from_raster(str(dem_path))
    dem = grid.read_raster(str(dem_path))

    started = time.time()
    log.info("hydrology_start", cells=int(dem.shape[0] * dem.shape[1]))

    pit_filled = grid.fill_pits(dem)
    flooded = grid.fill_depressions(pit_filled)
    inflated = grid.resolve_flats(flooded)
    log.info("dem_conditioned", seconds=round(time.time() - started, 1))

    fdir = grid.flowdir(inflated)
    acc = grid.accumulation(fdir)
    log.info("flow_routed", seconds=round(time.time() - started, 1))

    drainage = acc > accum_threshold
    fraction = float(drainage.sum()) / drainage.size
    log.info(
        "drainage_derived",
        threshold_cells=accum_threshold,
        drainage_fraction=round(fraction, 4),
        note="expect a small percentage; tens of percent means the threshold is wrong",
    )

    # pysheds' own implementation of "walk downslope to the first drainage
    # cell". 'iterative' avoids recursion limits on a multi-million-cell grid.
    hand = grid.compute_hand(fdir, inflated, drainage, algorithm="iterative")
    hand = np.asarray(hand, dtype="float32")

    # Tiny negatives are interpolation noise around channels, not real.
    hand = np.where(hand < 0, 0.0, hand)

    log.info("hand_computed", seconds=round(time.time() - started, 1))
    return hand, np.asarray(drainage), fraction


def vectorise_drainage(
    drainage: np.ndarray, transform, utm_epsg: int, out_path: Path
) -> int:
    """Drainage cells to polygons in EPSG:4326 for P5's distance/crossing tests."""
    geoms = [
        shapely_shape(geom)
        for geom, value in rio_shapes(
            drainage.astype("uint8"), mask=drainage, transform=transform
        )
        if value == 1
    ]
    if not geoms:
        raise RuntimeError("Drainage network is empty — lower --accumulation-threshold.")

    gdf = gpd.GeoDataFrame(geometry=geoms, crs=f"EPSG:{utm_epsg}").to_crs(epsg=4326)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG", layer="drainage")
    log.info("drainage_vectorised", features=len(gdf), path=str(out_path))
    return len(gdf)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _stats(array: np.ndarray) -> tuple[float, float, float]:
    finite = array[np.isfinite(array)]
    return float(np.min(finite)), float(np.median(finite)), float(np.max(finite))


def run(
    city_slug: str,
    variant: str | None,
    buffer_deg: float,
    accum_threshold: int,
    force: bool,
) -> int:
    cfg = get_city(city_slug)
    settings.ensure_dirs()
    city_id = ensure_city(cfg, variant)

    aoi = cfg.bbox_for(variant)
    buffered = (
        aoi[0] - buffer_deg,
        aoi[1] - buffer_deg,
        aoi[2] + buffer_deg,
        aoi[3] + buffer_deg,
    )
    derived = settings.city_derived_dir(cfg.slug)
    suffix = f"_{variant}" if variant else ""
    cache_path = settings.cache_dir / "dem" / f"{cfg.slug}{suffix}_buffered.tif"
    work_dem = derived / "_work_dem_buffered.tif"

    with ingestion_run("copernicus_dem", city_id) as handle:
        # 1-2. Windowed, cached read of the buffered AOI.
        geo_array, geo_transform = fetch_buffered_dem(buffered, cache_path, force)

        # 3. Reproject the buffered grid to the metric CRS.
        dem_buf, tr_buf = reproject_to_metric(geo_array, geo_transform, cfg.utm_epsg)

        # Guard: drop hydrology to 60 m rather than miss the deadline.
        hydro_res = TARGET_RES_M
        if dem_buf.size > MAX_HYDRO_CELLS:
            log.warning(
                "downsampling_for_hydrology",
                cells=int(dem_buf.size),
                note="HAND computed at 60 m and resampled back; accuracy loss accepted",
            )
            dem_buf = dem_buf[::2, ::2]
            tr_buf = tr_buf * tr_buf.scale(2, 2)
            hydro_res = TARGET_RES_M * 2
            handle.note("HAND computed at 60 m (grid size guard)")

        # The AOI window into the buffered grid, computed once and shared.
        aoi_gdf = gpd.GeoDataFrame(
            geometry=gpd.GeoSeries.from_wkt(
                [f"POLYGON(({aoi[0]} {aoi[1]},{aoi[2]} {aoi[1]},{aoi[2]} {aoi[3]},{aoi[0]} {aoi[3]},{aoi[0]} {aoi[1]}))"]
            ),
            crs="EPSG:4326",
        ).to_crs(epsg=cfg.utm_epsg)
        window = aoi_window(tr_buf, dem_buf.shape, aoi_gdf.total_bounds)
        aoi_transform = rasterio.windows.transform(window, tr_buf)
        crs = CRS.from_epsg(cfg.utm_epsg)

        # 3b. dem.tif — the reference grid every other raster must match.
        dem_aoi = clip(dem_buf, window)
        write_raster(derived / "dem.tif", dem_aoi, aoi_transform, crs)
        log.info(
            "reference_grid_written",
            path=str(derived / "dem.tif"),
            shape=list(dem_aoi.shape),
            crs=f"EPSG:{cfg.utm_epsg}",
        )

        # 4. Slope, from the buffered DEM so edge cells have real neighbours.
        slope_aoi = clip(compute_slope(dem_buf, hydro_res), window)
        write_raster(derived / "slope.tif", slope_aoi, aoi_transform, crs)

        # 5. HAND on the buffered grid, then clipped.
        write_raster(work_dem, dem_buf, tr_buf, crs)
        hand_buf, drainage_buf, fraction = compute_hand(work_dem, accum_threshold)
        hand_aoi = clip(hand_buf, window)
        write_raster(derived / "hand.tif", hand_aoi, aoi_transform, crs, nodata=float("nan"))

        # 6. Drainage vector, clipped to the AOI.
        features = vectorise_drainage(
            clip(drainage_buf, window), aoi_transform, cfg.utm_epsg, derived / "drainage.gpkg"
        )
        work_dem.unlink(missing_ok=True)

        # 7. Fail loudly on implausible HAND, and assert one shared grid.
        e_lo, e_med, e_hi = _stats(dem_aoi)
        s_lo, s_med, s_hi = _stats(slope_aoi)
        h_lo, h_med, h_hi = _stats(hand_aoi)

        for name, arr in (("slope", slope_aoi), ("hand", hand_aoi)):
            if arr.shape != dem_aoi.shape:
                raise RuntimeError(
                    f"{name}.tif shape {arr.shape} != dem.tif {dem_aoi.shape}; "
                    "downstream stages assert a shared grid."
                )

        if not (HAND_MEDIAN_MIN_M <= h_med <= HAND_MEDIAN_MAX_M):
            raise RuntimeError(
                f"Median HAND is {h_med:.1f} m, outside the plausible "
                f"{HAND_MEDIAN_MIN_M}-{HAND_MEDIAN_MAX_M} m band. Flow routing has "
                "failed and every downstream number would be garbage. Check the "
                "accumulation threshold and that the DEM buffer is large enough."
            )

        handle.records = int(dem_aoi.size)
        handle.note(
            f"drainage_fraction={fraction:.4f}; drainage_features={features}; "
            f"hydro_res={hydro_res:.0f}m; buffer={buffer_deg}deg"
        )

    print(f"\n  reference grid : {dem_aoi.shape[1]} x {dem_aoi.shape[0]} @ {TARGET_RES_M:.0f} m, EPSG:{cfg.utm_epsg}")
    print(f"  drainage       : {fraction * 100:.2f}% of AOI, {features:,} features\n")
    print(f"  {'':<12}{'min':>10}{'median':>10}{'max':>10}")
    print(f"  {'-' * 42}")
    print(f"  {'elevation':<12}{e_lo:>10.1f}{e_med:>10.1f}{e_hi:>10.1f}   m")
    print(f"  {'slope':<12}{s_lo:>10.2f}{s_med:>10.2f}{s_hi:>10.2f}   deg")
    print(f"  {'HAND':<12}{h_lo:>10.1f}{h_med:>10.1f}{h_hi:>10.1f}   m")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build DEM, slope, HAND and drainage.")
    parser.add_argument("--city", default="abuja")
    parser.add_argument("--variant", default="municipal")
    parser.add_argument("--buffer-deg", type=float, default=DEFAULT_BUFFER_DEG)
    parser.add_argument(
        "--accumulation-threshold", type=int, default=DEFAULT_ACCUM_THRESHOLD
    )
    parser.add_argument("--force", action="store_true", help="Ignore the DEM cache.")
    args = parser.parse_args(argv)
    return run(
        args.city, args.variant, args.buffer_deg, args.accumulation_threshold, args.force
    )


if __name__ == "__main__":
    sys.exit(main())
