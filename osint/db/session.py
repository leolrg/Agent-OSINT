"""SQLAlchemy engine + session factory.

Reads DATABASE_URL from the environment. Synchronous (psycopg) — the
worker is single-threaded per task, so async SQLAlchemy adds complexity
without benefit. Phase 2's FastAPI will add a separate async engine.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _engine_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


_engine = create_engine(_engine_url(), pool_pre_ping=True, future=True)
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=_engine, autoflush=False, expire_on_commit=False, future=True
)


@contextmanager
def db_session() -> Iterator[Session]:
    """Context manager — commits on clean exit, rolls back on exception."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
