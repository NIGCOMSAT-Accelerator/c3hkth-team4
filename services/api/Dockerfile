# Build context is the repo root so packages/core can be installed editable.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Core first: it changes least, so this layer stays cached across rebuilds.
COPY packages/core/pyproject.toml /app/packages/core/pyproject.toml
COPY packages/core/core/__init__.py /app/packages/core/core/__init__.py
RUN pip install -e /app/packages/core

COPY services/api/pyproject.toml /app/services/api/pyproject.toml
COPY services/api/api/__init__.py /app/services/api/api/__init__.py
RUN pip install -e /app/services/api && pip install pytest

# Real source arrives via bind mount in compose; copied here so the image
# also stands alone for deployment (P10).
COPY packages/core /app/packages/core
COPY services/api /app/services/api

# The prebuilt routing graph (~11 MB). With this in the image the API needs no
# persistent disk: topology comes from here, risk from the database. Rebuild it
# with `processing.ingest.roads` if the AOI or road filter ever changes.
COPY data/cache/osm/abuja_municipal_routing.graphml /app/data/cache/osm/

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
