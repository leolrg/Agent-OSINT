"""A NextAuth-signed JWT verifies in the FastAPI current_user dependency.

Guards against NEXTAUTH_SECRET / algorithm drift between Next.js and Python.
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from jose import jwt

from osint.api.dependencies import User, current_user


SECRET = "test-secret-32-bytes-or-more-padding-padding-padding"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    app = FastAPI()

    @app.get("/me")
    def me(u: User = Depends(current_user)) -> dict:
        return {"id": u.id, "email": u.email}

    return TestClient(app)


def _sign(claims: dict) -> str:
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_valid_jwt_in_cookie_is_accepted(client):
    user_id = str(uuid.uuid4())
    token = _sign({"sub": user_id, "email": "a@example.com",
                   "iat": int(time.time()), "exp": int(time.time()) + 3600})
    r = client.get("/me", cookies={"next-auth.session-token": token})
    assert r.status_code == 200
    assert r.json() == {"id": user_id, "email": "a@example.com"}


def test_authorization_bearer_also_works(client):
    token = _sign({"sub": "u1", "email": "a@example.com",
                   "exp": int(time.time()) + 3600})
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_missing_cookie_is_401(client):
    r = client.get("/me")
    assert r.status_code == 401


def test_bad_signature_is_401(client):
    token = jwt.encode({"sub": "u1", "exp": int(time.time()) + 3600},
                       "different-secret", algorithm="HS256")
    r = client.get("/me", cookies={"next-auth.session-token": token})
    assert r.status_code == 401


def test_expired_token_is_401(client):
    token = _sign({"sub": "u1", "exp": int(time.time()) - 60})
    r = client.get("/me", cookies={"next-auth.session-token": token})
    assert r.status_code == 401


def test_secure_prefix_cookie_is_also_accepted(client):
    token = _sign({"sub": "u1", "email": "a@example.com",
                   "exp": int(time.time()) + 3600})
    r = client.get("/me", cookies={"__Secure-next-auth.session-token": token})
    assert r.status_code == 200
