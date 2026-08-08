"""P4 — Water Observations from Space (WOfS) all-time summary, clipped to a city.

WOfS encodes, from ~40 years of Landsat, how often each pixel was observed as
water. It is our floodplain prior: a road crossing ground that has been wet
5% of the time for four decades is in a fundamentally different position from
one that has never been observed wet. It is free, and it is pure satellite
evidence — no modelling, no interpolation, just what the sensors saw.

  python -m processing.ingest.wofs --city abuja --variant municipal

Collection ids and band names are DISCOVERED from the API, never hardcoded.
A guessed name that 404s at 2 AM is not a debuggable failure.
"""

from __future__ import annotations

import argparse
import sys

import httpx
import numpy as np
import rasterio
from odc.geo.geobox import GeoBox
from odc.stac import configure_rio
from odc.stac import load as odc_load
from pystac_client import Client
from rasterio.crs import CRS

from core.config import get_city, settings
from core.logging import configure_logging, get_logger
from processing.common import ensure_city, ingestion_run
from processing.ingest.terrain import write_raster

configure_logging("processing")
log = get_logger("wofs")

STAC_URL = "https://explorer.digitalearth.africa/stac"
# DE Africa's public data bucket lives in af-south-1 and is readable unsigned.
AWS_S3_ENDPOINT = "s3.af-south-1.amazonaws.com"

PERMANENT_WATER_THRESHOLD = 0.8


# --------------------------------------------------------------------------
# Discovery — never hardcode ids or bands
# --------------------------------------------------------------------------


def list_collection_ids() -> list[str]:
    """Every collection this STAC server offers.

    The documented route is the /collections endpoint. This server serves HTML
    there regardless of the Accept header, which is why pystac_client's
    get_collections() cannot parse it. The root catalog's `child` links carry
    the same information as JSON, so we fall back to those rather than
    hardcoding anything.
    """
    try:
        return sorted(c.id for c in Client.open(STAC_URL).get_collections())
    except Exception as exc:  # noqa: BLE001 — any parse/transport failure is a fallback trigger
        log.warning(
            "stac_collections_endpoint_unusable",
            error=str(exc)[:120],
            note="server returns HTML for /collections; using root catalog child links",
        )

    root = httpx.get(STAC_URL, timeout=60).json()
    ids = [
        link["href"].rstrip("/").rsplit("/", 1)[-1]
        for link in root.get("links", [])
        if link.get("rel") == "child" and link.get("href")
    ]
    if not ids:
        raise RuntimeError(f"{STAC_URL} exposed no collections via /collections or child links.")
    return sorted(ids)


def select_wofs_collection(collection_ids: list[str]) -> str:
    """Pick the WOfS all-time summary by matching, and say what we saw."""
    log.info("stac_collections_available", count=len(collection_ids))
    water = [c for c in collection_ids if "wofs" in c.lower()]
    log.info("stac_wofs_candidates", candidates=water)

    for cid in collection_ids:
        low = cid.lower()
        if "wofs" in low and "summary" in low and ("alltime" in low or "all_time" in low):
            log.info("stac_collection_selected", collection=cid)
            return cid

    raise RuntimeError(
        "No WOfS all-time summary collection found.\n"
        f"  WOfS-like collections offered: {water or 'none'}\n"
        f"  All {len(collection_ids)} collections: {collection_ids}"
    )


def select_frequency_band(asset_keys: list[str]) -> str:
    """Pick the frequency band from an item's assets, and say what we saw."""
    log.info("stac_item_assets", assets=asset_keys)
    for key in asset_keys:
        if "freq" in key.lower():
            log.info("stac_band_selected", band=key)
            return key
    raise RuntimeError(
        f"No frequency-like band among the item's assets: {asset_keys}. "
        "WOfS summaries normally expose 'frequency' alongside count_wet/count_clear."
    )


# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------


def load_frequency(bbox, geobox: GeoBox) -> np.ndarray:
    """Search, discover the band, and load onto the reference grid exactly."""
    configure_rio(
        cloud_defaults=True,
        aws={"aws_unsigned": True},
        AWS_S3_ENDPOINT=AWS_S3_ENDPOINT,
    )

    collection = select_wofs_collection(list_collection_ids())

    client = Client.open(STAC_URL)
    items = list(client.search(collections=[collection], bbox=list(bbox)).items())
    if not items:
        raise RuntimeError(f"No {collection} items intersect bbox {bbox}.")
    log.info("stac_items_found", count=len(items), collection=collection)

    band = select_frequency_band(list(items[0].assets))

    # geobox pins the load to dem.tif's grid, so the result matches the
    # reference by construction rather than by a later reprojection.
    log.info("wofs_load_start", items=len(items), shape=tuple(geobox.shape))
    dataset = odc_load(items, bands=[band], geobox=geobox, resampling="average")

    array = dataset[band]
    if "time" in array.dims:
        # An all-time summary carries one epoch; the tiles mosaic within it.
        array = array.max(dim="time")
    values = np.asarray(array.values, dtype="float32")
    log.info("wofs_loaded", shape=list(values.shape))
    return values


def run(city_slug: str, variant: str | None, force: bool) -> int:
    cfg = get_city(city_slug)
    settings.ensure_dirs()
    city_id = ensure_city(cfg, variant)

    derived = settings.city_derived_dir(cfg.slug)
    dem_path = derived / "dem.tif"
    if not dem_path.exists():
        raise SystemExit(
            f"{dem_path} is missing. Run processing.ingest.terrain first — "
            "dem.tif is the reference grid every other raster must match."
        )

    suffix = f"_{variant}" if variant else ""
    cache_path = settings.cache_dir / "wofs" / f"{cfg.slug}{suffix}_frequency.tif"

    with rasterio.open(dem_path) as dem:
        ref_shape, ref_transform, ref_crs = dem.shape, dem.transform, dem.crs
        geobox = GeoBox(dem.shape, dem.transform, dem.crs)

    with ingestion_run("deafrica_wofs", city_id) as handle:
        if cache_path.exists() and not force:
            log.info("wofs_cache_hit", path=str(cache_path))
            with rasterio.open(cache_path) as src:
                freq = src.read(1)
        else:
            if settings.demo_mode:
                raise RuntimeError(
                    f"DEMO_MODE is on and {cache_path} is missing. The demo never "
                    "downloads; run once with DEMO_MODE=false to populate the cache."
                )
            freq = load_frequency(cfg.bbox_for(variant), geobox)
            write_raster(cache_path, freq, ref_transform, ref_crs, nodata=float("nan"))
            log.info("wofs_cached", path=str(cache_path))

        # Hard rule 3: assert the shared grid rather than assume it.
        if freq.shape != ref_shape:
            raise RuntimeError(
                f"WOfS grid {freq.shape} != dem.tif {ref_shape}. Every raster in "
                "this project must share the reference grid exactly."
            )

        write_raster(derived / "wofs_freq.tif", freq, ref_transform, ref_crs, nodata=float("nan"))

        permanent = (np.nan_to_num(freq, nan=0.0) > PERMANENT_WATER_THRESHOLD).astype("uint8")
        write_raster(derived / "permanent_water.tif", permanent, ref_transform, ref_crs, nodata=0)

        finite = freq[np.isfinite(freq)]
        pct_permanent = 100.0 * permanent.sum() / permanent.size
        handle.records = int(freq.size)
        handle.note(
            f"permanent_water_pct={pct_permanent:.3f}; "
            f"valid_cells={finite.size}/{freq.size}"
        )

    # Re-open what we wrote and compare against dem.tif — the acceptance check.
    with rasterio.open(derived / "wofs_freq.tif") as w, rasterio.open(dem_path) as d:
        same = (
            w.shape == d.shape
            and w.transform.almost_equals(d.transform)
            and w.crs == d.crs
        )

    print(f"\n  grid matches dem.tif : {'YES' if same else 'NO'}  {w.shape} EPSG:{ref_crs.to_epsg()}")
    if finite.size:
        print(
            f"  frequency            : min {finite.min():.3f}  "
            f"median {np.median(finite):.3f}  max {finite.max():.3f}"
        )
    print(f"  valid cells          : {finite.size:,} / {freq.size:,} "
          f"({100 * finite.size / freq.size:.1f}%)")
    print(f"\n  AOI with freq > {PERMANENT_WATER_THRESHOLD}   : {pct_permanent:.2f}%  "
          f"(permanent water)\n")

    if not same:
        raise SystemExit("Grid assertion failed against dem.tif.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clip the DE Africa WOfS all-time summary.")
    parser.add_argument("--city", default="abuja")
    parser.add_argument("--variant", default="municipal")
    parser.add_argument("--force", action="store_true", help="Ignore the WOfS cache.")
    args = parser.parse_args(argv)
    return run(args.city, args.variant, args.force)


if __name__ == "__main__":
    sys.exit(main())
