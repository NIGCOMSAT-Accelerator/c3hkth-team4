"""P4 unit tests for the discovery logic. No network."""

from __future__ import annotations

import pytest

from processing.ingest.wofs import select_frequency_band, select_wofs_collection

# The 3 WOfS-like ids this server actually offered, out of 106 collections.
REAL_CANDIDATES = ["wofs_ls", "wofs_ls_summary_alltime", "wofs_ls_summary_annual"]


def test_selects_the_alltime_summary_not_the_annual_or_scene_collection():
    """`wofs_ls` is per-scene and `_annual` is per-year; we want the all-time prior."""
    assert select_wofs_collection(REAL_CANDIDATES) == "wofs_ls_summary_alltime"


def test_selection_is_not_order_dependent():
    assert select_wofs_collection(list(reversed(REAL_CANDIDATES))) == "wofs_ls_summary_alltime"


def test_tolerates_an_all_time_spelling_variant():
    assert select_wofs_collection(["wofs_ls_summary_all_time"]) == "wofs_ls_summary_all_time"


def test_missing_collection_fails_loudly_and_lists_what_was_available():
    """The failure message is the whole point: it must be debuggable at 2 AM."""
    with pytest.raises(RuntimeError) as exc:
        select_wofs_collection(["s2_l2a", "gm_s2_annual"])
    message = str(exc.value)
    assert "s2_l2a" in message and "gm_s2_annual" in message


def test_selects_frequency_from_the_real_asset_list():
    assert select_frequency_band(["count_wet", "frequency", "count_clear"]) == "frequency"


def test_missing_band_fails_loudly_and_lists_the_assets():
    with pytest.raises(RuntimeError) as exc:
        select_frequency_band(["count_wet", "count_clear"])
    assert "count_wet" in str(exc.value)
