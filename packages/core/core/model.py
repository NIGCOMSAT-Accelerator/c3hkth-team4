"""The model card: weights, thresholds and feature provenance.

Single source of truth, imported by BOTH the processing layer (which applies
the weights) and the API layer (which serves them at /v1/meta/model). A model
whose published weights can drift from its applied weights is not transparent,
it is just documented.
"""

from __future__ import annotations

# --- Static susceptibility (P5 stage B) ---------------------------------
SUSCEPTIBILITY_WEIGHTS: dict[str, float] = {
    "hand": 0.40,
    "wofs": 0.25,
    "slope": 0.15,
    "drainage": 0.15,
    "crossing": 0.05,
}

SEGMENT_BUFFER_M = 15.0
WOFS_BUFFER_M = 200.0

# --- Daily risk (P7 part A) ---------------------------------------------
RISK_WEIGHTS: dict[str, float] = {
    "base": 0.50,
    "wetness": 0.20,
    "interaction": 0.30,
}

WETNESS_SATURATION_MM = 120.0
TRIGGER_SATURATION_MM = 50.0
ANTECEDENT_DAYS = 7

BAND_LOW_MAX = 33
BAND_MEDIUM_MAX = 66

RISK_FORMULA = (
    "risk = 100 * clip(0.50*base + 0.20*wetness + 0.30*(base*trigger), 0, 1); "
    "base = susceptibility_pctile/100, wetness = min(rain_7d/120, 1), "
    "trigger = min(rain_24h_forecast/50, 1)"
)

INTERACTION_RATIONALE = (
    "The base*trigger term is deliberate. Heavy rain on high ground is not the "
    "same event as heavy rain in a floodplain, and a purely additive model "
    "cannot express that difference."
)

FEATURES: list[dict[str, object]] = [
    {
        "name": "hand_min",
        "weight": 0.40,
        "source": "Copernicus DEM GLO-30 via pysheds D8 flow routing",
        "unit": "m",
        "rationale": (
            "Height Above Nearest Drainage — the strongest single predictor of "
            "fluvial flood exposure. Minimum over the segment because a road is "
            "impassable at its lowest point."
        ),
    },
    {
        "name": "wofs_freq_max",
        "weight": 0.25,
        "source": "DE Africa WOfS all-time summary (~40 years of Landsat)",
        "unit": "fraction 0-1",
        "rationale": (
            "Floodplain prior from pure observation. Sampled over a 200 m buffer, "
            "not 15 m: it measures the floodplain the road sits in, not the road "
            "surface, which is dry land by construction."
        ),
    },
    {
        "name": "slope_mean",
        "weight": 0.15,
        "source": "Copernicus DEM GLO-30",
        "unit": "degrees",
        "rationale": "Flat ground sheds water slowly and ponds; steep ground drains.",
    },
    {
        "name": "dist_to_drainage_m",
        "weight": 0.15,
        "source": "Drainage network vectorised from flow accumulation",
        "unit": "m",
        "rationale": (
            "Proximity to a channel. Distinct from HAND: a road can be close to a "
            "channel but well above it, or far from any channel in a flat basin."
        ),
    },
    {
        "name": "crosses_drainage",
        "weight": 0.05,
        "source": "Intersection with the drainage network",
        "unit": "boolean",
        "rationale": (
            "Culverts and low-water crossings are where roads actually fail. Small "
            "weight because it is a coarse binary flag, not because the effect is small."
        ),
    },
]

LIMITATIONS: list[str] = [
    "No drainage-infrastructure data: culvert capacity, blockage and pumping are invisible to us.",
    "No ground truth against reported road-flooding incidents.",
    "HAND assumes fluvial flooding and under-weights pluvial (surface-water) flooding "
    "in built-up areas with poor drainage.",
    "The index is a relative ranking across this road network, not a probability or a depth.",
    "CHIRPS runs ~54 days behind real time on DE Africa, so today's antecedent rainfall "
    "comes from Open-Meteo. The exact split is reported per run.",
    "Sentinel-1 revisit is 12 days, so validation reflects the scenes available, not a "
    "continuous record.",
]
