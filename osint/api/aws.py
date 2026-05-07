"""boto3 client builders shared across API routes.

Reads AWS_REGION and AWS_ENDPOINT_URL (LocalStack) from env. Same env
contract as the Phase 1 worker.
"""
from __future__ import annotations

import os
from functools import lru_cache

import boto3


@lru_cache(maxsize=1)
def s3_client():
    return boto3.client(
        "s3",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
    )
