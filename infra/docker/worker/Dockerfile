# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps for psycopg + lxml/maigret/etc. (lean: only what fails install otherwise)
# pkg-config + libcairo2-dev are required to build pycairo (transitive dep of maigret).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates \
        pkg-config libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching.
COPY pyproject.toml /app/
COPY osint/__init__.py /app/osint/__init__.py
RUN pip install -e ".[dev]" || true  # tolerate dev deps; refined below

# Now copy the rest of the package.
COPY osint /app/osint
COPY scripts /app/scripts

# Re-install once full source is present so editable install indexes everything.
RUN pip install -e "."

# Worker entrypoint.
CMD ["python", "-m", "osint.worker.main"]
