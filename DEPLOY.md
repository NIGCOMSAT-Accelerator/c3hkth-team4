# Deploying to Render

Roughly 30 minutes, most of it waiting on the first Docker build. Two steps
cannot be automated in `render.yaml` — seeding the database, and telling each
service the other's URL.

**Architecture on Render**

| Piece | Render service | Why |
|---|---|---|
| API | Docker web service | Carries its routing graph in the image |
| Frontend | Static site | Vite build, free, never spins down |
| Database | Managed Postgres + PostGIS | Seeded once from a local dump |
| Daily scoring | Cron job, 04:30 WAT | Re-scores every segment against today's rainfall |
| Alerts | Cron job *(optional)* | Only if you want alerts firing in production |

**No persistent disk is needed.** The API's only runtime file dependency is the
11 MB routing graph, which is committed and baked into the image. Everything
else it serves comes from Postgres. Verified: the image boots and routes with
no volume mounted at all.

---

## Before you start

```bash
# 1. The routing graph must be committed — it is how the deployed API gets its
#    topology. ~11 MB, deterministic output of processing.ingest.roads.
git ls-files --error-unmatch data/cache/osm/abuja_municipal_routing.graphml

# 2. A portable database dump. NOT a plain pg_dump: the local PostGIS image
#    ships the tiger geocoder, and its schemas will not restore onto a managed
#    Postgres. `make dump-db` already restricts the dump to our own tables.
make dump-db          # -> deploy/seed.sql.gz, about 10 MB

# 3. Everything green locally before you ship it anywhere.
make smoke
```

Push to GitHub. Render deploys from the repo, so the graph and the code must
be on the branch you point it at. `deploy/seed.sql.gz` is gitignored — you
push it nowhere; you pipe it into the database in step 3 below.

---

## 1. Create the Blueprint

Render dashboard → **New** → **Blueprint** → select this repository.

Render reads `render.yaml` and proposes the database, the API and the static
site. It will ask for the values marked `sync: false`; leave them blank for
now, you will fill them in step 4.

Approve. The first API build takes 5–10 minutes — it is compiling the geo
stack — and the static site a minute or two.

## 2. Enable PostGIS

The dump contains geometry columns, so the extension has to exist before you
restore. Copy the **External Database URL** from the database's dashboard page.

```bash
export DB='postgres://...paste the external URL...'

# No local psql? Borrow the one in the compose stack.
docker compose exec -T db psql "$DB" -c 'CREATE EXTENSION IF NOT EXISTS postgis;'
docker compose exec -T db psql "$DB" -c 'SELECT PostGIS_Version();'
```

If `CREATE EXTENSION` is refused, the instance does not have PostGIS available
— check the database's Postgres version in Render; 16 does.

## 3. Seed the database

```bash
gunzip -c deploy/seed.sql.gz | docker compose exec -T db psql "$DB"

# Confirm what landed.
docker compose exec -T db psql "$DB" -tAc \
  "SELECT 'segments '||count(*) FROM road_segments
   UNION ALL SELECT 'risk rows '||count(*) FROM segment_risk;"
# expect: segments 42914 / risk rows 85828
```

**Do not run the ingestion pipeline in production.** It would re-download the
Copernicus DEM and recompute HAND on a small instance for no benefit — the
derived data is a deterministic function of inputs you already processed.

## 4. Cross-wire the two URLs

Render has now given you two addresses, something like
`https://climatepass-api.onrender.com` and
`https://climatepass-web.onrender.com`.

On the **API** service → Environment:

| Key | Value |
|---|---|
| `API_CORS_ORIGINS` | the web URL |
| `WEB_BASE_URL` | the web URL *(alert deep links)* |

On the **web** static site → Environment:

| Key | Value |
|---|---|
| `VITE_API_BASE` | the API URL |

`VITE_API_BASE` is compiled in by Vite at build time, so after setting it you
must **Clear build cache & deploy** the static site, not merely restart it.
This is the single most common way this deployment goes wrong.

## 5. Verify

```bash
API=https://climatepass-api.onrender.com ./scripts/smoke.sh
```

Ten checks in a couple of seconds, including the one that matters: two
distinct routes with a real delay and a real risk reduction. Then open the web
URL and run one route by hand.

---

## Things that will bite you

**Free instances sleep.** On Render's free plan a web service spins down after
15 minutes idle, and the next request pays roughly a 50-second cold start.
That is precisely what a judge clicking your link will hit. `render.yaml`
therefore puts the API on `starter` (~$7/month). If you must stay free, open
the link yourself two minutes before anyone else does.

**Free Postgres is deleted after 30 days.** Fine for a hackathon; not fine if
this is meant to survive. Move to a paid instance if it is.

**The alerts cron writes to an ephemeral disk.** With SMTP unconfigured the
`.eml` files render to `data/outbox/` and are then thrown away when the job
ends. Either configure `SMTP_HOST` and friends, or — better for the pitch —
demo alerts locally, where the outbox persists and you can open a real file.
The cron is commented as optional in `render.yaml` for exactly this reason.

**Live data, and the one decision it forces on you.** The `climatepass-daily`
cron re-scores all 42,914 segments at 04:30 WAT against the current day's
rainfall, and the API serves the most recent scored date — so the deployment
advances on its own. This is cheap: susceptibility is already in the database
and is NOT recomputed, so the job touches no rasters and no DEM. It fetches
rainfall and runs one SQL statement, about ten seconds.

`DEMO_DATE` is therefore left UNSET on the deployment. Set it only to freeze
the view, and understand the trade-off before you do:

| | Live (`DEMO_DATE` unset) | Pinned (`DEMO_DATE=2026-08-02`) |
|---|---|---|
| Shows | Today, whatever the weather | A wet day: 6,489 High segments |
| Honest? | Completely | Yes, and the date is visible in the UI |
| Demo risk | A dry day shows **zero** High-risk roads | None |

Measured on a dry day (2026-08-08: 2.8 mm forecast) the model produced no High
band at all — which is the model behaving correctly and refusing to invent
danger, but it is a poor thing to demonstrate a router on. Run live, and pin
only while recording. The served date is shown on every page and at
`/health/routing`, so a pinned view never masquerades as today.

**The database seed is a starting point, not the product.** After the first
daily run the deployment holds its own current data; the seed only exists so
the API has something to serve before 04:30 comes around.

**Region.** `frankfurt` is the closest Render region to Nigeria. Expect
roughly 150–250 ms of latency from Abuja; the routing call itself is a few
milliseconds of that.

---

## Deploying somewhere else

The same two properties make this portable to Fly.io, Cloud Run or a plain VM:
the API image is self-contained, and the database is an ordinary PostGIS.

For a VM with your own domain and TLS, use the compose overlay instead:

```bash
PUBLIC_DOMAIN=climatepass.example.ng \
PUBLIC_ORIGIN=https://climatepass.example.ng \
WEBHOOK_HMAC_SECRET=$(openssl rand -hex 32) \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

That brings up Caddy with automatic certificates in front of the API and a
static nginx serving the frontend. See `deploy/Caddyfile`.
