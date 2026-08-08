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

COPY packages/core/pyproject.toml /app/packages/core/pyproject.toml
COPY packages/core/core/__init__.py /app/packages/core/core/__init__.py
RUN pip install -e /app/packages/core

COPY services/alerts/pyproject.toml /app/services/alerts/pyproject.toml
COPY services/alerts/alerts/__init__.py /app/services/alerts/alerts/__init__.py
RUN pip install -e /app/services/alerts && pip install pytest

COPY packages/core /app/packages/core
COPY services/alerts /app/services/alerts

# P9 replaces this with the APScheduler worker (alerts.worker).
CMD ["tail", "-f", "/dev/null"]
