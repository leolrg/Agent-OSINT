# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates \
        pkg-config libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/
COPY osint/__init__.py /app/osint/__init__.py
COPY osint /app/osint

RUN pip install -e "."

EXPOSE 8000
CMD ["uvicorn", "osint.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
