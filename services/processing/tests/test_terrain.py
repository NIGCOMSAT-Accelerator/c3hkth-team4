"""P3 unit tests. Pure functions only — no network, no rasters on disk."""

from __future__ import annotations

import numpy as np
import pytest

from core.config import get_city
from processing.ingest.terrain import (
    DEFAULT_BUFFER_DEG,
    HAND_MEDIAN_MAX_M,
    HAND_MEDIAN_MIN_M,
    compute_slope,
    tile_prefix,
    tiles_for_bbox,
)


def test_tile_prefix_matches_the_real_bucket_layout():
    """Verified against a live listing of s3://copernicus-dem-30m."""
    assert tile_prefix(9, 7) == "Copernicus_DSM_COG_10_N09_00_E007_00_DEM/"
    assert tile_prefix(8, 7) == "Copernicus_DSM_COG_10_N08_00_E007_00_DEM/"


def test_tile_prefix_handles_southern_and_western_hemispheres():
    assert tile_prefix(-3, -60).startswith("Copernicus_DSM_COG_10_S03_00_W060_00")


def test_tiles_for_bbox_covers_every_intersecting_degree_cell():
    # Municipal Abuja spans one degree of longitude and two of latitude.
    assert sorted(tiles_for_bbox((7.25, 8.9, 7.62, 9.22))) == [(8, 7), (9, 7)]
    # A bbox inside a single cell needs exactly one tile.
    assert tiles_for_bbox((7.3, 9.1, 7.4, 9.2)) == [(9, 7)]


def test_default_buffer_does_not_pull_in_extra_dem_tiles():
    """The 0.1 degree hydrology buffer is sized to stay inside two tiles.

    Widening it is legitimate, but at ~0.15 degrees the AOI crosses into E006
    and the download cost jumps by half. Bandwidth is this project's binding
    constraint, so that trade-off should be made deliberately, not by accident.
    """
    aoi = get_city("abuja").bbox_for("municipal")
    buffered = (
        aoi[0] - DEFAULT_BUFFER_DEG,
        aoi[1] - DEFAULT_BUFFER_DEG,
        aoi[2] + DEFAULT_BUFFER_DEG,
        aoi[3] + DEFAULT_BUFFER_DEG,
    )
    assert sorted(tiles_for_bbox(buffered)) == [(8, 7), (9, 7)]


def test_slope_of_a_flat_surface_is_zero():
    flat = np.full((8, 8), 412.0, dtype="float32")
    assert np.allclose(compute_slope(flat, 30.0), 0.0)


def test_slope_of_a_known_ramp_is_the_expected_angle():
    """A surface rising 30 m per 30 m cell is a 45 degree slope."""
    ramp = np.tile(np.arange(8, dtype="float32") * 30.0, (8, 1))
    slope = compute_slope(ramp, 30.0)
    # Interior cells only: np.gradient uses one-sided differences at the edges.
    assert np.allclose(slope[1:-1, 1:-1], 45.0, atol=1e-3)


def test_slope_scales_with_cell_size():
    """Same array, coarser cells, gentler slope — guards the cell_size argument."""
    ramp = np.tile(np.arange(8, dtype="float32") * 30.0, (8, 1))
    coarse = compute_slope(ramp, 60.0)
    assert coarse[4, 4] == pytest.approx(np.degrees(np.arctan(0.5)), abs=1e-3)


def test_hand_sanity_band_brackets_the_observed_median():
    """Abuja's measured median HAND is 15.0 m; the guard must not reject it."""
    assert HAND_MEDIAN_MIN_M < 15.0 < HAND_MEDIAN_MAX_M
