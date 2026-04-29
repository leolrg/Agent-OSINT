#!/usr/bin/env bash
set -euo pipefail

awslocal s3 mb s3://agent-osint-local-results
awslocal sqs create-queue \
  --queue-name agent-osint-scans \
  --attributes '{"VisibilityTimeout":"5400"}'  # 90 min
echo "[init] localstack bucket and queue created"
