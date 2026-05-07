"""Worker config from env. All Phase 3 production values come from
AWS Secrets Manager + ECS env vars; the names match here so Phase 3
just changes the source, not the code."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerConfig:
    database_url: str
    redis_url: str
    aws_region: str
    aws_endpoint_url: str | None
    s3_bucket: str
    sqs_queue_url: str
    visibility_timeout_seconds: int
    heartbeat_seconds: int
    log_level: str

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            redis_url=os.environ["REDIS_URL"],
            aws_region=os.environ["AWS_REGION"],
            aws_endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
            s3_bucket=os.environ["S3_BUCKET"],
            sqs_queue_url=os.environ["SQS_QUEUE_URL"],
            visibility_timeout_seconds=int(os.environ.get("SCAN_VISIBILITY_TIMEOUT_SECONDS", "5400")),
            heartbeat_seconds=int(os.environ.get("SCAN_HEARTBEAT_SECONDS", "300")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
