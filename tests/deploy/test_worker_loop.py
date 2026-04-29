"""Unit tests for the worker SQS-consume loop using moto + fakeredis.

Phase 1 stub: the worker consumes a message, claims the scan in DB,
publishes scan.started + scan.completed, acks. Real run_scan() call
arrives in Task 10.
"""
from __future__ import annotations

import json
import os
import uuid
from unittest.mock import patch

import boto3
import fakeredis
import pytest
from moto import mock_aws

from osint.db.models import Base, Scan, User
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.integration


@pytest.fixture
def db(pg_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    yield Sess


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr("osint.worker.main._redis_client", lambda url: fake)
    return fake


def _seed_user_and_scan(Sess) -> tuple[uuid.UUID, uuid.UUID]:
    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush()
        sc = Scan(user_id=u.id, status="queued", agent="critic_react_v3",
                  params={"subject": "Jane Doe"})
        s.add(sc); s.flush()
        s.commit()
        return u.id, sc.id


@mock_aws
def test_consume_one_message_marks_running_then_completed(db, aws_env, fake_redis,
                                                          monkeypatch):
    sqs = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs.create_queue(QueueName="t")["QueueUrl"]
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="agent-osint-test")

    monkeypatch.setenv("SQS_QUEUE_URL", queue_url)
    monkeypatch.setenv("S3_BUCKET", "agent-osint-test")
    monkeypatch.setenv("SCAN_VISIBILITY_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("SCAN_HEARTBEAT_SECONDS", "30")

    user_id, scan_id = _seed_user_and_scan(db)
    sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({
        "scan_id": str(scan_id), "user_id": str(user_id),
        "params": {"subject": "Jane Doe", "agent": "critic_react_v3"},
    }))

    # Stub run_scan so Task 9's test doesn't need a real LLM.
    with patch("osint.worker.run_scan.execute_scan", return_value={
        "ok": True,
        "result_bytes": b'{"scan": "stub"}',
        "total_cost_usd": 0.0,
        "total_tool_calls": 0,
    }):
        from osint.worker.main import run_once
        run_once()

    with db() as s:
        sc = s.get(Scan, scan_id)
        assert sc.status == "completed"
        assert sc.s3_key is not None
        assert sc.completed_at is not None
