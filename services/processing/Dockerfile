# Build context is the repo root so packages/core can be installed editable.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # rasterio/pyogrio/shapely ship their own GDAL/GEOS in the wheels.
    GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY packages/core/pyproject.toml /app/packages/core/pyproject.toml
COPY packages/core/core/__init__.py /app/packages/core/core/__init__.py
RUN pip install -e /app/packages/core

COPY services/processing/pyproject.toml /app/services/processing/pyproject.toml
COPY services/processing/processing/__init__.py /app/services/processing/processing/__init__.py
RUN pip install -e /app/services/processing && pip install pytest

COPY packages/core /app/packages/core
COPY services/processing /app/services/processing

# Worker container: no long-running process yet. Pipelines are invoked with
# `docker compose exec processing python -m processing.ingest.roads --city abuja`
CMD ["tail", "-f", "/dev/null"]
