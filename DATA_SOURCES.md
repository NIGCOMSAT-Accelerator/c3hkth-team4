# Data sources

Every dataset ClimatePass AI uses, what it contributes, and what it costs you to use it. Latency figures are **measured on this deployment**, not quoted from documentation — where a feed is staler than advertised we say so here rather than let it surprise someone later.

---

## 1. Copernicus DEM GLO-30 — elevation, slope, HAND

| | |
|---|---|
| **Provider** | European Space Agency / European Union, produced by Airbus Defence and Space |
| **Access** | `s3://copernicus-dem-30m/` (eu-central-1), public, **no credentials** |
| **Resolution** | 1 arc-second (~30 m) |
| **Coverage** | Global |
| **Latency** | Static product. Source acquisitions 2010–2015 (TanDEM-X) |
| **Format** | Cloud Optimized GeoTIFF, 1°×1° tiles |
| **Licence** | Free, full, open access under the Copernicus licence. Attribution **mandatory** |
| **Volume used** | 2 tiles (N08/E007, N09/E007), ~87 MB total — but we range-request only the AOI window, ~12% of each |

**Role in the model.** The single most important input. We derive three products:
- `dem.tif` — the **reference grid** every other raster must match exactly (1361 × 1186 @ 30 m, EPSG:32632)
- `slope.tif` — degrees, from the gradient
- `hand.tif` — **Height Above Nearest Drainage**, via pysheds: fill pits → fill depressions → resolve flats → D8 flow direction → flow accumulation → walk downslope to the first drainage cell

HAND carries weight **0.40**, the largest of any term.

**Required attribution string** — reproduce verbatim:

> Produced using Copernicus WorldDEM™-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

**Caveat that matters:** GLO-30 is a Digital *Surface* Model. It includes buildings and tree canopy, not bare earth. In dense urban blocks HAND is therefore biased high (the surface sits above the true ground), which makes built-up segments look marginally safer than they are. We do not correct for this.

---

## 2. Digital Earth Africa WOfS — water observation frequency

| | |
|---|---|
| **Provider** | Digital Earth Africa (DE Africa), on Landsat 5/7/8/9 |
| **Access** | STAC at `https://explorer.digitalearth.africa/stac`, data in `s3://deafrica-services` (af-south-1), unsigned |
| **Collection** | `wofs_ls_summary_alltime`, band `frequency` — **discovered from the API, never hardcoded** |
| **Resolution** | 30 m |
| **Temporal span** | 1984 → present, expressed as `1984--P42Y` (42 years) |
| **Latency** | All-time summary, republished periodically. Not a live feed |
| **Licence** | CC BY 4.0 |

**Role in the model.** The floodplain prior, weight **0.25**. It answers a question no model can: where has water actually, observably been? Sampled over a **200 m** buffer of each segment rather than 15 m, because it measures the floodplain a road sits in, not the road surface — which is dry land by construction, that being why a road is there.

**Measured contribution, stated honestly:** 0.52% of the AOI exceeds frequency 0.8 (permanent water — Jabi Lake, Lower Usuma Dam). Correlation with the final index is only **+0.122**. WOfS is inherently sparse over a road network and discriminates strongly only where roads run beside standing water (Alex Ekwueme Way, alongside Jabi Lake, returns 0.526). See [MODEL.md](MODEL.md).

**Attribution:** Contains modified Copernicus/Landsat data processed by Digital Earth Africa.

---

## 3. CHIRPS — satellite-derived antecedent rainfall

| | |
|---|---|
| **Provider** | Climate Hazards Center, UC Santa Barbara, via DE Africa |
| **Access** | Same STAC endpoint, collection `rainfall_chirps_daily`, asset `data` |
| **Resolution** | 0.05° (~5.5 km) |
| **Latency** | **54 days measured** on 2026-08-08 (newest available 2026-06-15) |
| **Licence** | Public domain / open, no restriction on use |

**Role, and the honest problem.** CHIRPS blends satellite thermal-infrared cold-cloud-duration estimates with station data, which is what would make our rainfall input genuinely space-derived.

**On this feed it is 54 days behind real time.** For a 7-day antecedent window on any recent date it therefore contributes **zero days**, and Open-Meteo supplies all of it. Every scoring run records the exact split — `antecedent: chirps:0d, open-meteo:7d; chirps_latency_days=54` — in `ingestion_runs.notes`, and serves it at `GET /v1/meta/model`. Anyone asking "is this really satellite rainfall?" gets the true answer from the API rather than from a slide.

---

## 4. Open-Meteo — forecast and antecedent gap-fill

| | |
|---|---|
| **Provider** | Open-Meteo |
| **Access** | `https://api.open-meteo.com/v1/forecast`, **no API key** |
| **Resolution** | ~11 km, hourly |
| **Latency** | Live; 14 days of past data plus 3 days forecast in one call |
| **Licence** | CC BY 4.0. Free tier is non-commercial |

**Role.** Two jobs: the 24-hour forecast that drives the `trigger` term, and — because of the CHIRPS latency above — the entire 7-day antecedent window in practice.

**Not satellite data.** Open-Meteo is a numerical weather prediction and reanalysis blend. We do not present it as EO, and the distinction is disclosed at `/v1/meta/model`.

**Hindcast note.** When scoring a historical date the trigger uses the rainfall actually observed the following day, labelled `perfect-forecast hindcast`. A hindcast flatters a forecasting system and must never be reported as a live prediction.

**Commercial use requires their paid tier.**

---

## 5. OpenStreetMap — the road network

| | |
|---|---|
| **Provider** | OpenStreetMap contributors, fetched via `osmnx` |
| **Access** | Overpass API; cached to `data/cache/osm/abuja.graphml` |
| **Latency** | Live, community-maintained |
| **Licence** | **ODbL 1.0** — share-alike. Derived geographic databases must be released under ODbL |
| **Volume** | 233,371 edges for the full FCT; 20,355 after AOI and class filtering |

**Role.** The network itself. Edges are split into ≤120 m segments (mean 88 m), children retaining the parent `osm_way_id`, `u_node`, `v_node` — which is how per-segment risk maps back onto the routing graph at 99.9% coverage.

**Licence obligation, taken seriously:** ODbL is share-alike. `road_segments` is a derived database of OSM, so redistributing it obliges you to release it under ODbL and attribute OpenStreetMap. Risk scores computed *from* it are produced works and may be licensed differently, but the geometry cannot be quietly relicensed.

**Attribution:** © OpenStreetMap contributors, ODbL.

---

## 6. Nominatim — geocoding (optional)

| | |
|---|---|
| **Provider** | OpenStreetMap Foundation |
| **Access** | `https://nominatim.openstreetmap.org/search` |
| **Licence** | ODbL. **Usage policy: max 1 request/second, User-Agent required** |

**Role.** Secondary. A bundled gazetteer of Abuja landmarks in `packages/core/core/cities.yaml` is tried first and is authoritative under `DEMO_MODE`. A live geocoder rate-limiting us mid-pitch must not break the product.

---

## Summary

| Source | Satellite? | Weight in model | Latency | Licence |
|---|---|---|---|---|
| Copernicus DEM GLO-30 | **Yes** | 0.40 (HAND) + 0.15 (slope) + 0.15 (drainage) | static | Copernicus, attribution required |
| DE Africa WOfS | **Yes** | 0.25 | periodic | CC BY 4.0 |
| CHIRPS | **Yes** | rainfall (0 days in practice) | **54 days** | open |
| Open-Meteo | No | rainfall (all of it in practice) | live | CC BY 4.0, non-commercial |
| OpenStreetMap | No | network geometry | live | **ODbL, share-alike** |

**70% of the susceptibility index derives directly from Copernicus DEM.** That is the honest centre of gravity of this system, and any claim that leans harder on the rainfall side than that would be overstating it.

---

## Caching and offline operation

Every external fetch caches to `data/cache/`. This is not an optimisation — it is what makes `DEMO_MODE` possible, and it is enforced: `docker-compose.demo.yml` puts the containers on an `internal: true` network with no route to the internet, and `scripts/verify-demo.sh` proves the whole demo path still passes. Run `make demo-verify`.
