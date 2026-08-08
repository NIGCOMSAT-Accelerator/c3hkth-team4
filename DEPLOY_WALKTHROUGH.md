# Render, click by click

`DEPLOY.md` explains *why* the deployment is shaped the way it is. This file is
just the buttons, in order. Follow it top to bottom.

You will end up with three URLs and a database. Budget ~30 minutes, most of it
waiting on the first build.

---

## Step 0 — put the code on GitHub

**Render cannot see your laptop.** It deploys from a Git repository, so this
has to happen first. There is currently no remote configured.

In your terminal, from the project folder:

```bash
gh auth login          # choose GitHub.com → HTTPS → login with a browser
gh repo create climatepass-ai --private --source=. --remote=origin --push
```

No `gh`? Do it in the browser instead: github.com → **New repository** → name
it `climatepass-ai` → **Private** → *do not* add a README → **Create**. Then:

```bash
git remote add origin https://github.com/YOUR-USERNAME/climatepass-ai.git
git push -u origin main
```

Confirm it worked — you should see one URL and no error:

```bash
git remote -v
```

> The push includes an 11 MB routing graph. That is intentional: it is how the
> deployed API gets its road topology without a persistent disk.

---

## Step 1 — create the Blueprint

1. Go to **dashboard.render.com** and sign in.
2. Top right: **New +** → **Blueprint**.
3. If this is your first time, Render asks to connect GitHub. Click
   **Connect GitHub**, authorise it, and grant access to the repo you just
   created (either *All repositories* or select `climatepass-ai`).
4. Back in Render, your repo appears in the list. Click **Connect** next to it.
5. Render finds `render.yaml` and shows a **Blueprint Name** field plus a list
   of what it will create:

   ```
   climatepass-db       PostgreSQL
   climatepass-api      Web Service (Docker)
   climatepass-web      Static Site
   climatepass-daily    Cron Job
   climatepass-alerts   Cron Job
   ```

6. It then asks you to fill in the variables marked `sync: false`.

   **You do not have any URLs yet, and that is fine.** Render generates them
   — `https://climatepass-api-a1b2.onrender.com` and similar — exactly like
   Vercel does. It just cannot generate them before the services exist, and
   this form runs first. So leave them empty now and set the one that matters
   in step 4.

   | Service | Key | Enter now |
   |---|---|---|
   | climatepass-api | `DEMO_DATE` | *empty* |
   | climatepass-api | `API_CORS_ORIGINS` | `*` |
   | climatepass-api | `WEB_BASE_URL` | *empty* |
   | climatepass-web | `VITE_API_BASE` | *empty* |
   | climatepass-alerts | `DEMO_DATE` | *empty* |
   | climatepass-alerts | `WEB_BASE_URL` | *empty* |
   | climatepass-alerts | `WEBHOOK_HMAC_SECRET` | any long random string |

   Only `API_CORS_ORIGINS` needs a value now, and `*` is fine for a hackathon
   — it means the API accepts requests from any origin, which saves you a
   chicken-and-egg problem. Tighten it later if this outlives the event.

   Empty `DEMO_DATE` is deliberate: it makes the deployment serve live data
   rather than one frozen day.

   > **Why can't Render wire this automatically?** Its `fromService` reference
   > returns a service's *private network* hostname, which a browser cannot
   > reach. The frontend runs in your user's browser and needs the public
   > address, so that one value has to be set by hand.

7. Click **Apply** (or **Create New Resources**).

Render now builds everything. The API takes **5–10 minutes** — it is compiling
the geospatial stack. Watch it under the `climatepass-api` service → **Logs**.

**Cron jobs cost money on Render.** If you would rather not pay yet, delete the
two `- type: cron` blocks from `render.yaml` before step 1 and add them later.
Without `climatepass-daily` the data stops advancing, which is fine for a few
days since the seed carries real scored data.

---

## Step 2 — turn on PostGIS

Our data is geographic, so the database needs the PostGIS extension before it
will accept the seed.

1. In Render, click **climatepass-db**.
2. Scroll to **Connections** and copy the **External Database URL**. It looks
   like `postgres://climatepass:xxxx@dpg-xxxx.frankfurt-postgres.render.com/climatepass`.

Back in your terminal:

```bash
export DB='postgres://...paste the whole thing...'

# You may not have psql installed; borrow the one already running in Docker.
docker compose exec -T db psql "$DB" -c 'CREATE EXTENSION IF NOT EXISTS postgis;'
docker compose exec -T db psql "$DB" -c 'SELECT PostGIS_Version();'
```

The second command should print a version like `3.4 USE_GEOS=1 ...`. If it
does, move on.

---

## Step 3 — load the data

```bash
gunzip -c deploy/seed.sql.gz | docker compose exec -T db psql "$DB"
```

It prints a lot of `COPY 42914`-style lines. Then check it landed:

```bash
docker compose exec -T db psql "$DB" -tAc \
  "SELECT 'segments '||count(*) FROM road_segments
   UNION ALL SELECT 'risk rows '||count(*) FROM segment_risk;"
```

Expect **segments 42914** and **risk rows 85828** (two scored days). If you get zero rows, the
seed did not apply — re-run the gunzip line and read the error.

---

## Step 4 — tell the frontend where the API is

**This is the one manual wiring step, and it is a single variable.**

Every service now has a real URL, shown at the top of its page in Render:

```
climatepass-api   https://climatepass-api-a1b2.onrender.com
climatepass-web   https://climatepass-web-c3d4.onrender.com
```

Copy the **api** URL. Then on **climatepass-web** → **Environment**:

| Key | Set to |
|---|---|
| `VITE_API_BASE` | your api URL, e.g. `https://climatepass-api-a1b2.onrender.com` |

No trailing slash.

Then — this part is not optional — at the top of the `climatepass-web` page
click **Manual Deploy** → **Clear build cache & deploy**.

> Vite compiles `VITE_API_BASE` into the JavaScript when the site is built, so
> a restart does nothing; the site has to be rebuilt. Skip this and the page
> loads but every request fails. If that happens the app now tells you so
> directly — it will say the build has no API address.

**Optional, once you have the URLs.** Neither of these blocks anything:

- `climatepass-api` → `WEB_BASE_URL` = the web URL (used for deep links inside
  alert emails)
- `climatepass-api` → `API_CORS_ORIGINS` = the web URL instead of `*`, if you
  would rather not leave the API open to any origin

---

## Step 5 — check it works

```bash
API=https://your-api-url.onrender.com ./scripts/smoke.sh
```

Ten checks, a couple of seconds. All ten should pass. The important one is the
last pair: two distinct routes with a real delay, and `lambda 0` returning the
fastest route unchanged.

Then open your **web** URL and run one route by hand — Gwarinpa → Kubwa is the
good one.

---

## If something is wrong

**The API build fails.** Open `climatepass-api` → **Logs**. If it mentions
`data/cache/osm/abuja_municipal_routing.graphml`, the routing graph did not
reach GitHub. Check with `git ls-files data/cache/osm/` and push it.

**The site says "This build has no API address".** `VITE_API_BASE` was empty
when the site was built. Set it and use **Clear build cache & deploy** — a
plain restart will not pick it up.

**The site says "Could not reach the ClimatePass API at https://…".** The
address is set but the API is not answering. Check that `climatepass-api` is
live, and open that URL with `/health` on the end — it should return
`{"status":"ok"}`. On the free plan the first request can take ~50 s while the
service wakes.

**Browser console shows a CORS error.** `API_CORS_ORIGINS` on the API does not
match your web URL. It must include the scheme and no trailing slash:
`https://climatepass-web-a1b2.onrender.com`.

**Everything 500s and the logs mention `postgres://`.** The database URL did
not get wired. Check `DATABASE_URL` exists on the API service — it should have
been set automatically from the database.

**First request takes ~50 seconds.** The service is on the free plan and went
to sleep. Either upgrade `climatepass-api` to Starter, or open the link
yourself a couple of minutes before anyone else does.

**The site says "Not Found" at the root URL, but the deploy succeeded.**
Render built the site but cannot find `index.html` where it was told to look.
Fix it on the service itself rather than waiting for a blueprint sync:
`climatepass-web` → **Settings** and check two fields —

| Field | Must be |
|---|---|
| Root Directory | `apps/web` |
| Publish Directory | `dist` |

If Root Directory is blank, Publish Directory has to be `apps/web/dist`
instead. Set them, then **Manual Deploy → Clear build cache & deploy**.

> Editing `render.yaml` alone will NOT change an already-created service.
> A blueprint is applied when resources are created; afterwards you either
> edit the service in the dashboard, or go to the Blueprint and re-sync.

**The map shows no roads.** Check `/health/routing` on your API URL — if
`"loaded": false`, the routing graph is missing from the image. Same fix as the
build failure above.

---

## What you should have at the end

```
https://climatepass-web-….onrender.com     the app
https://climatepass-api-….onrender.com     the API
https://climatepass-api-….onrender.com/docs        interactive API docs
https://climatepass-api-….onrender.com/v1/meta/model   weights and provenance
```

The last one is worth putting in front of judges directly — it is the
transparency endpoint, and it answers "where did this number come from?"
without you having to say a word.
