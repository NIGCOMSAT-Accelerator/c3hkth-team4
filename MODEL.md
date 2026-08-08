# The model

How ClimatePass AI turns satellite observation into a road flood risk score, why each weight is what it is, and — the section that matters most — what this model cannot see.

The constants below live in `packages/core/core/model.py` and are imported by **both** the pipeline that applies them and the API that publishes them at `GET /v1/meta/model`. A model whose published weights can drift from its applied weights is not transparent, it is merely documented.

---

## 1. Susceptibility — where water collects

Static per segment. Recomputed when the terrain inputs change, not daily.

### Features

Each is percentile-ranked across the whole city before weighting. That choice is deliberate: it makes the index an honest **relative ordering of this road network** rather than an absolute flood depth, which is not something we could produce from these inputs without inventing precision we do not have.

| Term | Weight | Source | Physical justification |
|---|---|---|---|
| `1 − pctile(hand_min)` | **0.40** | Copernicus DEM → pysheds D8 | Height Above Nearest Drainage is the strongest single predictor of fluvial flood exposure. It answers the question that decides passability: how far above the nearest channel is this road? Two metres floods; forty does not, however hard it rains. We take the segment **minimum**, not the mean — a road is impassable at its lowest point, and averaging a dip out of existence is precisely the error that drives people into water. |
| `pctile(wofs_freq_max)` | 0.25 | DE Africa WOfS | Where satellite has actually observed water across 42 years of Landsat. Pure observation: no model, no interpolation. Ground repeatedly wet will be wet again. **Maximum** over the segment, for the same reason HAND uses minimum. |
| `1 − pctile(slope_mean)` | 0.15 | Copernicus DEM | Flat ground sheds water slowly and ponds; steep ground drains. Mean is right here — drainage is a property of the segment as a whole. |
| `1 − pctile(dist_to_drainage_m)` | 0.15 | Vectorised flow accumulation | Proximity to a channel, which is genuinely distinct from HAND: a road can sit close to a channel but well above it, or far from any channel in a flat basin. Together they describe position far better than either alone. |
| `crosses_drainage` | 0.05 | Geometric intersection | Where a road crosses a channel there is a culvert or a low-water crossing, and that is where roads actually fail — the structure blocks with debris and overtops. Small weight because it is a coarse binary flag, not because the effect is small. |

### Sampling geometry

Terrain features use a **15 m** buffer — roads are 10–20 m wide, so that samples the carriageway.

WOfS uses **200 m**, and this was a correction made on evidence. At 15 m the term was near-inert: only 0.1% of segments exceeded frequency 0.05, because a road surface is dry land by construction — that is why a road is there. Widening to 200 m makes it a floodplain-context measure instead:

| Buffer | segments > 0.05 | 99th percentile value |
|---|---|---|
| 15 m | 0.1% | 0.008 |
| 100 m | 0.8% | 0.025 |
| **200 m** | **2.4%** | **0.314** |
| 300 m | 3.9% | 0.645 |

300 m was rejected: at that distance the buffer can cross a ridge into a different catchment, and the physical story stops being defensible.

### Measured behaviour

Correlation of each feature with the resulting index across 42,914 segments:

| Feature | Correlation | Reading |
|---|---|---|
| `hand_min` | **−0.842** | Dominant, as designed. Negative because low HAND means high risk |
| `dist_to_drainage_m` | −0.702 | Strong, as designed |
| `slope_mean` | −0.162 | Weak but correct sign |
| `wofs_freq_max` | **+0.122** | Weak — see limitations |

**Face validity, both directions.** Most susceptible: Abuja–Keffi Expressway (HAND 1.9 m), Mpape–Shishipe (1.8 m), Ameyo Adadevoh Way (5.1 m) — all documented flood-prone. Least susceptible: Sunrise Boulevard (33.1 m), Maitama Roundabout (28.4 m), Asokoro Roundabout (19.8 m) — the elevated ridge districts. A model that ranked Maitama as flood-prone would be visibly wrong, and it does not.

---

## 2. Daily risk — whether there is water to collect

```
base    = susceptibility_pctile / 100
wetness = min(rain_7d_mm / 120, 1)
trigger = min(rain_24h_forecast_mm / 50, 1)

risk    = 100 × clip(0.50·base + 0.20·wetness + 0.30·(base × trigger), 0, 1)
```

| Weight | Term | Why |
|---|---|---|
| 0.50 | `base` | Terrain dominates. A road's position does not change with the weather. |
| 0.20 | `wetness` | Antecedent saturation. Ground already wet sheds further rain rather than absorbing it. Saturates at 120 mm over 7 days. |
| 0.30 | `base × trigger` | **The interaction, and the point of the model.** |

Heavy rain on high ground is not the same event as heavy rain in a floodplain. A purely additive model cannot express that: it would add the same rainfall contribution to Maitama as to Abuja–Keffi. The product term means incoming rain only elevates risk *in proportion to how exposed the location already is*. Saturates at 50 mm in 24 hours.

**Bands:** 0–33 Low · 34–66 Medium · 67–100 High

### Worked example — 2026-08-02 (real observed weather)

88.5 mm antecedent, 17.3 mm the following day → `wetness = 0.738`, `trigger = 0.346`.

| | Low | Medium | High | max |
|---|---|---|---|---|
| segments | 12,970 | 23,455 | 6,489 | 75.1 |

---

## 3. Route risk

```
route_risk = 0.6 × max(segment_risk) + 0.4 × length_weighted_mean(segment_risk)
```

**Not a plain mean, and this is worth defending.** One impassable segment closes a route however good the other 30 km are. A mean would let a single 95-risk crossing disappear into an otherwise safe journey — hiding exactly the fact the user needs. Weighting the maximum at 0.6 means a route cannot look safe while it still crosses something impassable.

The consequence is that headline reduction percentages are conservative. On Gwarinpa → Kubwa the safer route drops mean segment risk from 69.5 to 48.3 — a 30% improvement — while `route_risk` falls only 11.8%, because both routes still touch a segment near the daily maximum. That understatement is the metric working as intended, not a defect.

### The safer route

Second Dijkstra pass over:

```
cost = travel_time × (1 + λ · (risk/100)²)
```

Squaring is deliberate. A linear penalty treats risk 20 as meaningfully worse than risk 10, which drivers do not; squaring tolerates mild risk and then rises sharply — matching how people actually behave, ignoring a puddle and refusing a flooded underpass.

- **λ default 3.0**, exposed as a request parameter so it can be adjusted live
- **λ = 0 reproduces the fastest route exactly** — the severe penalty below is gated on λ, so the control is honest at both ends
- Segments at or above **risk 70** cost **50×** rather than being deleted. Deleting can disconnect the graph and return no route at all, and "no route" is a worse answer than "here is a bad route, clearly marked"

The severe threshold is 70, not a higher number, so that it matches the High band. Colouring a segment High on the map while the router declines to route around it would be an inconsistency a user could reasonably question. At an earlier setting of 85 the rule was dormant: on real Abuja weather nothing reaches 85, so the router only nudged and the best achievable reduction across every landmark pair was 3.3%.

---

## 4. Validation status

**Sentinel-1 validation (P6) was not run.** It was first on the fallback ladder and was cut because scene-sized SAR downloads at the measured ~0.08 MB/s to AWS S3 would have taken hours.

This means **the weights are physically motivated but not empirically validated against observed inundation.** We state that plainly rather than implying otherwise. The evidence we do have is face validity in both directions (§1) and internal consistency of the feature correlations.

The intended validation, documented so it can be run later: search Sentinel-1 RTC for a wet-season scene, convert VV to dB, speckle-filter, Otsu-threshold constrained to −22…−13 dB, subtract permanent water, and compute ROC-AUC of susceptibility against observed inundation. `GET /v1/meta/model` returns `validation: {status: "not_run"}` until that happens — it does not return a fabricated figure.

---

## 5. Limitations

Named here before anyone else finds them.

1. **No drainage infrastructure data.** Culvert capacity, blockage state, pumping and drain maintenance are invisible to us. In a built-up city these frequently dominate whether a specific road floods, and we cannot see any of them.

2. **No ground truth.** Nothing is calibrated against reported road-flooding incidents. We have no incident dataset for Abuja, so we cannot report accuracy, precision or recall — and we do not.

3. **HAND assumes fluvial flooding.** It models water rising from channels. It under-weights **pluvial** (surface-water) flooding, where rain simply overwhelms drainage in place — which is a large share of real urban flooding.

4. **The DEM is a surface model.** GLO-30 includes buildings and canopy, not bare earth, so HAND is biased high in dense urban blocks, making built-up segments look marginally safer than they are.

5. **WOfS contributes little here (+0.122).** Roads are built on dry ground, so the term discriminates only where a road runs beside standing water. It carries weight 0.25 but earns less influence than that implies.

6. **Rainfall is not satellite-derived in practice.** CHIRPS runs 54 days behind, so Open-Meteo — a reanalysis/NWP blend — supplies the antecedent window. Disclosed per run and at `/v1/meta/model`.

7. **The score is a relative ranking, not a probability or a depth.** Risk 75 means "in the top few percent of this network today", not "75% chance of flooding" and not "0.75 m of water".

8. **Coverage is the arterial municipal network only** — 42,914 segments over 3,772 km. Residential streets are excluded (they are 80% of OSM's network and would have put the AOI at 212k segments). Local flooding on a residential street is invisible to this system.

9. **Single AOI.** Abuja municipal, not the full FCT. The full territory produced 401,659 segments, past the processing budget.

10. **Speeds are OSM defaults.** `osmnx` infers travel time from highway class, not from observed traffic. Delay figures are free-flow estimates, and Abuja traffic is not free-flow.

---

## 6. Reproducibility

Every pipeline stage writes an `ingestion_runs` row — source, status, record count, duration, and notes including rainfall provenance. `GET /v1/meta/model` returns the last 12. Every external fetch is cached, and `make demo-verify` proves the entire path reproduces with no network access at all.
