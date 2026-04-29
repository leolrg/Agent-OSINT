"""GET /api/scans/{id} — auth-gated scan detail with presigned S3 URL."""
from __future__ import annotations

import time
import uuid

import boto3
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from osint.api.app import create_app
from osint.db.models import Scan, User


pytestmark = pytest.mark.integration

SECRET = "test-secret-padding-padding-padding-padding"


def _sign(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "email": "a@example.com", "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


@pytest.fixture
def client_factory(pg_url, monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    def _factory():
        return TestClient(create_app())

    return _factory


@mock_aws
def test_owner_can_get_their_scan(client_factory, pg_url, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "agent-osint-test")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="agent-osint-test")
    s3.put_object(Bucket="agent-osint-test",
                  Key="scans/u/s.json", Body=b'{"hello": "world"}')

    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush()
        sc = Scan(user_id=u.id, status="completed", agent="react_v1",
                  params={"subject": "x"}, s3_key="scans/u/s.json")
        s.add(sc); s.flush()
        s.commit()
        user_id, scan_id = u.id, sc.id

    client = client_factory()
    r = client.get(
        f"/api/scans/{scan_id}",
        cookies={"next-auth.session-token": _sign(str(user_id))},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(scan_id)
    assert body["status"] == "completed"
    assert body["s3_url"] is not None  # presigned URL present


@mock_aws
def test_other_user_gets_403_with_404_shape(client_factory, pg_url, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "agent-osint-test")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="agent-osint-test")

    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    with Sess() as s:
        owner = User(email="owner@example.com", password_hash="x")
        intruder = User(email="other@example.com", password_hash="x")
        s.add_all([owner, intruder]); s.flush()
        sc = Scan(user_id=owner.id, status="completed", agent="react_v1",
                  params={}, s3_key="scans/u/s.json")
        s.add(sc); s.flush()
        s.commit()
        scan_id, intruder_id = sc.id, intruder.id

    client = client_factory()
    r = client.get(
        f"/api/scans/{scan_id}",
        cookies={"next-auth.session-token": _sign(str(intruder_id))},
    )
    # Both 403 and 404 are acceptable security-wise; we return 404 to avoid
    # leaking existence of the row.
    assert r.status_code in (403, 404)


def test_unauthenticated_is_401(client_factory):
    client = client_factory()
    r = client.get(f"/api/scans/{uuid.uuid4()}")
    assert r.status_code == 401


def test_unknown_scan_is_404(client_factory, pg_url):
    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush(); s.commit()
        user_id = u.id

    client = client_factory()
    r = client.get(
        f"/api/scans/{uuid.uuid4()}",
        cookies={"next-auth.session-token": _sign(str(user_id))},
    )
    assert r.status_code == 404
