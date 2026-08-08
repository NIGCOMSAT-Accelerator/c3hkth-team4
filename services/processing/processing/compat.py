"""Compatibility shims for third-party libraries.

Kept in one place so the reason for each patch survives the sprint.
"""

from __future__ import annotations

import numpy as np

from core.logging import get_logger

log = get_logger("compat")


def patch_numpy_for_pysheds() -> None:
    """pysheds 0.5 calls ``np.in1d``, which NumPy 2.0 removed.

    We run NumPy 2.4 because rasterio 1.4 and geopandas 1.1 are built against
    it — downgrading NumPy to suit pysheds would break the raster stack, which
    is the larger dependency. ``np.isin`` is NumPy's documented replacement.

    ``in1d`` flattens its first argument and ``isin`` preserves shape, so the
    shim reproduces the flattening rather than aliasing the two directly. Every
    pysheds call site already passes a ravelled array, but matching the real
    contract means this cannot surprise us if that changes.

    Must run before ``pysheds`` is imported.
    """
    if hasattr(np, "in1d"):
        return

    def in1d(ar1, ar2, assume_unique=False, invert=False):
        return np.isin(
            np.asarray(ar1).ravel(), ar2, assume_unique=assume_unique, invert=invert
        )

    np.in1d = in1d  # type: ignore[attr-defined]
    log.info("numpy_in1d_shim_installed", numpy=np.__version__)
