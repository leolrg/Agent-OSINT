"""Verify SQLAlchemy column metadata matches the Drizzle migration output.

The Drizzle SQL is the source of truth. SQLAlchemy in osint/db/models.py
must declare columns with matching names and base types. Catches drift
when one side gets edited but not the other.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect

from osint.db.models import Base


pytestmark = pytest.mark.integration


def test_sqlalchemy_metadata_matches_postgres(pg_url):
    engine = create_engine(pg_url)
    inspector = inspect(engine)
    pg_tables = {t for t in inspector.get_table_names() if not t.startswith("_")}

    sa_tables = set(Base.metadata.tables.keys())
    assert sa_tables == pg_tables, (
        f"SQLAlchemy and Postgres tables differ\n"
        f"  Only in SQLAlchemy: {sa_tables - pg_tables}\n"
        f"  Only in Postgres:   {pg_tables - sa_tables}"
    )

    for table in sa_tables:
        sa_cols = {c.name for c in Base.metadata.tables[table].columns}
        pg_cols = {c["name"] for c in inspector.get_columns(table)}
        assert sa_cols == pg_cols, (
            f"Column drift in `{table}`\n"
            f"  Only in SQLAlchemy: {sa_cols - pg_cols}\n"
            f"  Only in Postgres:   {pg_cols - sa_cols}"
        )
