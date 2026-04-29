"""Worker entrypoint.

Long-poll SQS for one message, run the scan, ack on success. ECS
runs `python -m osint.worker.main` which loops forever; tests call
run_once() directly.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3
import redis
import structlog

from osint.db.models import Scan, ScanRun
from osint.db.session import db_session
from osint.log import configure_logging
from osint.worker.config import WorkerConfig
from osint.worker import run_scan as run_scan_module
from osint.worker.event_sink import RedisEventSink


def _redis_client(url: str) -> redis.Redis:
    return redis.from_url(url)


def _sqs_client(cfg: WorkerConfig):
    return boto3.client(
        "sqs", region_name=cfg.aws_region,
        endpoint_url=cfg.aws_endpoint_url,
    )


def _s3_client(cfg: WorkerConfig):
    return boto3.client(
        "s3", region_name=cfg.aws_region,
        endpoint_url=cfg.aws_endpoint_url,
    )


def _configure_logging_with_event_sink(scan_id: str, redis_client) -> RedisEventSink:
    """Add RedisEventSink to the structlog processor chain in front of the renderer.

    osint/log.py installs ConsoleRenderer as the last processor; we splice
    our sink before it so we still get stdout logs *plus* Redis fanout.
    """
    configure_logging()
    cfg = structlog.get_config()
    procs = list(cfg["processors"])
    sink = RedisEventSink(scan_id=scan_id, redis_client=redis_client)
    # Insert before the renderer (last entry).
    procs.insert(len(procs) - 1, sink)
    structlog.configure(processors=procs,
                        wrapper_class=cfg["wrapper_class"],
                        logger_factory=cfg["logger_factory"])
    return sink


def _publish_terminal(redis_client, scan_id: str, event: str, **fields) -> None:
    payload = json.dumps({"ts": time.time(), "level": "info",
                          "event": event, "scan_id": scan_id, **fields})
    try:
        redis_client.publish(f"scan:{scan_id}", payload)
        redis_client.lpush(f"scan:{scan_id}:events", payload)
        redis_client.ltrim(f"scan:{scan_id}:events", 0, 99)
        redis_client.expire(f"scan:{scan_id}:events", 86400)
    except Exception:
        pass


def run_once() -> bool:
    """Process one SQS message. Returns True if a message was handled, False if none."""
    cfg = WorkerConfig.from_env()
    sqs = _sqs_client(cfg)
    s3 = _s3_client(cfg)
    rds = _redis_client(cfg.redis_url)

    resp = sqs.receive_message(
        QueueUrl=cfg.sqs_queue_url,
        WaitTimeSeconds=20,
        MaxNumberOfMessages=1,
        VisibilityTimeout=cfg.visibility_timeout_seconds,
    )
    msgs = resp.get("Messages") or []
    if not msgs:
        return False

    msg = msgs[0]
    body = json.loads(msg["Body"])
    scan_id = uuid.UUID(body["scan_id"])
    user_id = uuid.UUID(body["user_id"])
    params = body["params"]

    _configure_logging_with_event_sink(str(scan_id), rds)
    log = structlog.get_logger("worker").bind(scan_id=str(scan_id))

    # Claim: status=running + insert scan_runs row.
    with db_session() as s:
        sc = s.get(Scan, scan_id)
        if sc is None:
            log.error("scan.row_missing")
            sqs.delete_message(QueueUrl=cfg.sqs_queue_url, ReceiptHandle=msg["ReceiptHandle"])
            return True
        sc.status = "running"
        sc.started_at = datetime.now(timezone.utc)
        prev_attempts = s.query(ScanRun).filter_by(scan_id=scan_id).count()
        s.add(ScanRun(scan_id=scan_id, attempt=prev_attempts + 1,
                      worker_task=os.environ.get("HOSTNAME"),
                      started_at=datetime.now(timezone.utc)))
    log.info("scan.started", agent=params.get("agent"))

    try:
        outcome = run_scan_module.execute_scan(scan_id=str(scan_id), params=params)
    except Exception as e:  # noqa: BLE001
        log.exception("scan.failed", error=str(e))
        with db_session() as s:
            sc = s.get(Scan, scan_id)
            sc.status = "failed"
            sc.error_message = str(e)[:1000]
            sc.completed_at = datetime.now(timezone.utc)
        _publish_terminal(rds, str(scan_id), "scan.failed", error=str(e)[:200])
        sqs.delete_message(QueueUrl=cfg.sqs_queue_url, ReceiptHandle=msg["ReceiptHandle"])
        return True

    # Upload result to S3.
    s3_key = f"scans/{user_id}/{scan_id}.json"
    s3.put_object(Bucket=cfg.s3_bucket, Key=s3_key, Body=outcome["result_bytes"],
                  ContentType="application/json")

    with db_session() as s:
        sc = s.get(Scan, scan_id)
        sc.status = "completed"
        sc.s3_key = s3_key
        sc.total_cost_usd = outcome.get("total_cost_usd")
        sc.total_tool_calls = outcome.get("total_tool_calls")
        sc.completed_at = datetime.now(timezone.utc)

    _publish_terminal(rds, str(scan_id), "scan.completed", s3_key=s3_key)
    sqs.delete_message(QueueUrl=cfg.sqs_queue_url, ReceiptHandle=msg["ReceiptHandle"])
    log.info("scan.completed", s3_key=s3_key)
    return True


def main() -> int:
    cfg = WorkerConfig.from_env()
    logging.basicConfig(level=getattr(logging, cfg.log_level))
    stop = False

    def _on_signal(*_):
        nonlocal stop; stop = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    while not stop:
        try:
            run_once()
        except Exception:
            structlog.get_logger("worker").exception("worker.loop_error")
            time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
