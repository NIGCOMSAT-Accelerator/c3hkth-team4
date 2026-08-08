# ClimatePass AI

Built for the **NigComSat Accelerator 3.0 EO Hackathon**. Submission Sunday 9 August, 22:59 WAT. Code freeze Sunday 18:00 WAT.

Speed matters more than elegance. The three-layer architecture below is required by the brief and is **not negotiable**.

---

## Product

A **road-network flood exposure engine for Abuja FCT**. It scores every ~100m road segment for flood risk daily from satellite data, returns a risk score plus a safer alternative route between two points, and alerts users when a corridor they watch crosses their threshold.

The demo centrepiece is risk-aware routing: two routes, computed delay, computed risk reduction — *"12 minutes for 43% safer"*, calculated rather than hardcoded.

---

## The three-layer separation (brief §10)

This must be **physically visible in the repo**. Judges look for it. Do not collapse these.

| Directory | Layer | Responsibility |
|---|---|---|
| `services/processing` | 1 | EO ingestion, spatial processing, scoring (Python) |
| `services/alerts` | 2 | Thresholds, subscriptions, notification dispatch (Python) |
| `services/api` | 3 | Public REST API (FastAPI) |
| `apps/web` | — | React frontend |
| `packages/core` | — | Shared config, SQLAlchemy models, DB session |

Each service has its **own** `pyproject.toml` and its **own** dependency set — the separation is real in the build graph, not just in folder names. `packages/core` is installed editable into all three.

---

## Stack

Python 3.11 · PostGIS 16-3.4 · SQLAlchemy 2.0 (typed) · Alembic · FastAPI · React + TypeScript + Vite + MapLibre GL + TanStack Query + Tailwind

Geo: geopandas, shapely, rasterio, rioxarray, odc-stac, pystac-client, osmnx, networkx, rasterstats, pysheds

---

## Hard rules

1. **Every external fetch caches to `data/cache/`.** No exceptions. Re-runs must work with the network off — this is what makes DEMO_MODE possible, and the demo will be given on conference wifi.
2. **Never hardcode STAC collection ids or band names from memory.** Discover, log what is actually available, match by id/title, and fail with the list if nothing matches.
   Note for DE Africa: `explorer.digitalearth.africa/stac/collections` serves
   **HTML** regardless of the Accept header, so `pystac_client.get_collections()`
   cannot parse it. `/stac` (root) and `/stac/search` return JSON. `wofs.py`
   falls back to the root catalog's `child` links — reuse that helper rather
   than rediscovering this. Verified live: collection `wofs_ls_summary_alltime`,
   band `frequency`.
3. **`data/derived/<city>/dem.tif` is the reference grid.** Every other raster must match its shape, transform and CRS exactly. Assert it downstream.
4. **Metric math in EPSG:32632. Geometry stored as EPSG:4326.** Always.
5. **Never leave NULL susceptibility.** Segments without raster coverage get the city median. NULLs crash the router.
6. **Every pipeline stage writes an `ingestion_runs` row.** This table is a demo asset — we show it to prove automated ingestion.
7. **Only the product owner merges to main.** Everyone else works on branches.

---

## Layout

```
climatepass_ai/
├── packages/core/           # config, cities.yaml, db session, models, migrations
│   ├── core/
│   │   ├── config.py        # pydantic-settings + City loader
│   │   ├── cities.yaml      # Abuja bbox, centroid, EPSG 32632, AOI variants, landmarks
│   │   ├── db.py            # engine, SessionLocal, Base, session_scope
│   │   └── models.py        # P1 fills this
│   ├── alembic.ini
│   └── migrations/
├── services/processing/     # LAYER 1
│   └── processing/
│       ├── ingest/          # roads.py, terrain.py, wofs.py
│       ├── scoring/         # susceptibility_v1.py, susceptibility_v2.py, daily.py
│       └── validate/        # sentinel1.py
├── services/alerts/         # LAYER 2
├── services/api/            # LAYER 3
├── apps/web/                # React frontend
├── data/                    # bind-mounted, gitignored
│   ├── cache/               # every external fetch lands here
│   ├── derived/<city>/      # dem.tif, slope.tif, hand.tif, wofs_freq.tif, drainage.gpkg
│   └── outbox/              # simulated email delivery
└── docker-compose.yml
```

---

## Commands

```bash
make up          # build + start everything
make db-init     # alembic upgrade head
make seed        # full ingestion pipeline for abuja
make test        # pytest in the api container
make psql        # psql shell
make help        # all targets
```

Pipelines run inside the processing container:

```bash
docker compose exec processing python -m processing.ingest.roads --city abuja --stats
```

API on `localhost:8000` (docs at `/docs`), Postgres on host port **5434**.

---

## Scoring model

Two stages, **both writing the same column**, so nothing downstream ever breaks:

- **v1** (`susceptibility_v1.py`) — crude hand-weighted index from OSM attributes. Placeholder values, ships in 30 minutes to unblock the API and frontend tracks. Never reaches the demo.
- **v2** (`susceptibility_v2.py`) — real features from HAND, WOfS, slope and drainage. Replaces v1 in place.

v2 weights (all terms percentile-normalised across the city):

| Term | Weight | Rationale |
|---|---|---|
| `1 - pctile(hand_min)` | 0.40 | Height Above Nearest Drainage — strongest single flood predictor |
| `pctile(wofs_freq_max)` | 0.25 | ~40 years of Landsat water observations — the floodplain prior |
| `1 - pctile(slope_mean)` | 0.15 | Flat ground drains slowly |
| `1 - pctile(dist_to_drainage)` | 0.15 | Proximity to channels |
| `crosses_drainage` | 0.05 | Culverts and low-water crossings — where roads actually fail |

Daily risk layers rainfall on top:

```
base    = susceptibility_pctile / 100
wetness = min(rain_7d_mm / 120, 1)
trigger = min(rain_24h_forecast_mm / 50, 1)
risk    = 100 * clip(0.50*base + 0.20*wetness + 0.30*(base*trigger), 0, 1)
```

The `base*trigger` interaction is deliberate: heavy rain on high ground is not the same event as heavy rain in a floodplain, and a purely additive model cannot express that.

Bands: 0–33 Low · 34–66 Medium · 67–100 High.

---

## Fallback ladder

Cut in this order. **Never cut below line 4.**

1. P6 Sentinel-1 validation — costs a strong slide, nothing else
2. Alerts *delivery* — keep subscriptions + threshold evaluation, write to the table, show in UI
3. Results map layer toggles — ship the two routes only
4. **Never cut:** road segments · susceptibility from HAND/WOfS · daily rainfall scoring · risk-aware routing · the API · README + INTEGRATION.md

If behind at Sunday noon, take 1 and 2 immediately.

---

## CURRENT STATUS

Updated: **Sat 8 Aug, ~14:30 WAT**

| Prompt | Track | Status |
|---|---|---|
| P0 Bootstrap | B | ✅ done — scaffold, compose, health endpoint green |
| P1 Schema | B | ✅ done — 6 tables, SRID 4326 verified, 8 tests pass |
| P2 Road graph | A | ✅ done — 42,914 segments, 3,772 km, municipal AOI |
| P3 Terrain + HAND | A | ✅ done — dem/slope/hand/drainage, median HAND 15.0 m |
| P4 WOfS | A | ✅ done — grid matches dem.tif, 0.52% permanent water |
| P5 Scoring v1 | B | ✅ done — 42,914 scored, 0 NULLs. **Tracks B and C are unblocked** |
| P5 Scoring v2 | B | ✅ done — real evidence, 0 NULLs, HAND corr -0.842 |
| P6 Sentinel-1 | A | ⬜ optional, cut freely |
| P7 API + routing | B | ⬜ plan mode |
| P8 Web app | C | ⬜ starts hour 3 against v1 data |
| P9 Alerts | B | ⬜ |
| P10 Freeze + audit | D+B | ⬜ Sunday 16:00–18:00 |

**Bandwidth is the binding constraint on this machine, not compute.**
Measured: PyPI 0.09–0.90 MB/s (highly variable), AWS S3 ~0.08 MB/s. The
processing image took **45 minutes to build**, entirely download-bound. Plan
EO work around this:

- Never invalidate the pip layers in `services/processing/Dockerfile`. The
  `libexpat1`/`libgomp1` apt step sits *after* the pip installs for exactly
  this reason — read the comment there before editing.
- `data/cache/` is not an optimisation here, it is the schedule. Anything
  downloaded once must never be downloaded twice.
- Copernicus DEM over plain HTTPS is confirmed reachable and unauthenticated;
  tile naming `Copernicus_DSM_COG_10_N09_00_E007_00_DEM` is verified correct.
- P6 (Sentinel-1) pulls scene-sized rasters. At ~0.08 MB/s that is hours.
  It is already first on the fallback ladder — treat cutting it as the
  default, not the exception.

**Notes for the next session**

- Alembic is scaffolded but has **zero revisions**. `make db-init` is a no-op until P1 creates the first one.
- `packages/core/core/models.py` is an empty stub exposing `Base`. P1 fills it.
- **The AOI is decided: `municipal` + arterial classes only.** Always pass
  `--variant municipal` to the pipelines. Measured on the real graph:
  full FCT = 401,659 segments (5× over budget); municipal with residential
  streets = 211,997 (still 2.6× over); municipal arterial-only = **42,914**.
  Residential streets are 80% of OSM's network here, so excluding them — not
  shrinking the AOI — is what actually made the budget. `--include-residential`
  exists if that is ever revisited, but raise `--max-segments` with it.
- The full-FCT graphml is cached, and `load_graph` reuses a wider cached graph
  for a narrower AOI rather than re-downloading. Changing AOI costs ~20s, not
  6 minutes.
- **The reference grid exists**: `data/derived/abuja/dem.tif`, 1361 × 1186 @ 30 m,
  EPSG:32632. `slope.tif` and `hand.tif` are verified to share its shape,
  transform and CRS exactly. P4 must assert the same before writing WOfS.
- pysheds 0.5 calls `np.in1d`, removed in NumPy 2.0. `processing/compat.py`
  installs a shim that **must run before pysheds is imported** — see
  `terrain.compute_hand`. Do not "clean up" that import ordering.
- Hydrology runs on a 0.1° buffered DEM and only the outputs are clipped, so
  flow routing is not truncated at the AOI edge. The buffer is sized to stay
  inside the same two DEM tiles; widening it past ~0.15° pulls in E006.
- Postgres is on host port **5434** (5433 was occupied on the dev machine). Inside the compose network it is `db:5432` as normal.
- The DB image is `imresamu/postgis:16-3.4`, not `postgis/postgis` — the official one has no arm64 build and would run under emulation.
