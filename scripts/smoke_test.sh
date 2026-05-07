#!/usr/bin/env bash
# End-to-end smoke test for Phase 1.
# 1. Bring up the compose stack.
# 2. Seed a user + scan row in Postgres.
# 3. Drop an SQS message referencing that scan.
# 4. Poll Postgres for status='completed' (or 'failed') with timeout.
# 5. Tear down on success.
set -euo pipefail

if [ ! -f .env ]; then
    echo "ERROR: .env missing. Copy .env.example and fill in API keys." >&2
    exit 1
fi
# shellcheck disable=SC1091
source .env

echo "[1/5] Bringing up the stack..."
docker compose up -d --wait

echo "[2/5] Seeding user + scan row..."
SUBJECT="${SMOKE_SUBJECT:-Jane Doe smoke test}"
read -r USER_ID SCAN_ID < <(docker compose exec -T postgres psql -U app -d agent_osint -tA -F' ' \
    -v subject="$SUBJECT" -v timestamp="$(date +%s)" <<'SQL'
WITH new_user AS (
  INSERT INTO users (email, password_hash)
  VALUES (CONCAT('smoke-', :'timestamp', '@example.com'), 'x')
  RETURNING id
)
INSERT INTO scans (user_id, status, agent, params)
SELECT id, 'queued', 'react_v1',
       jsonb_build_object(
         'subject', :'subject',
         'agent', 'react_v1',
         'budget_usd', 0.50,
         'max_calls', 5,
         'max_seconds', 120
       )
FROM new_user
RETURNING user_id, id;
SQL
)
echo "  user_id=$USER_ID scan_id=$SCAN_ID"

echo "[3/5] Sending SQS message..."
docker run --rm --network agent-osint_default \
    -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test \
    amazon/aws-cli:latest sqs send-message \
    --endpoint-url http://localstack:4566 --region us-east-1 \
    --queue-url http://localstack:4566/000000000000/agent-osint-scans \
    --message-body "{\"scan_id\":\"$SCAN_ID\",\"user_id\":\"$USER_ID\",\"params\":{\"subject\":\"$SUBJECT\",\"agent\":\"react_v1\",\"budget_usd\":0.50,\"max_calls\":5,\"max_seconds\":120}}"

echo "[4/5] Waiting for scan to complete (max 300s)..."
DEADLINE=$(( $(date +%s) + 300 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    STATUS=$(docker compose exec -T postgres psql -U app -d agent_osint -tA \
        -c "SELECT status FROM scans WHERE id='$SCAN_ID';")
    echo "  status=$STATUS"
    if [ "$STATUS" = "completed" ]; then
        echo "[5/5] PASS — scan completed."
        docker compose exec -T postgres psql -U app -d agent_osint -tA \
            -c "SELECT s3_key, total_cost_usd FROM scans WHERE id='$SCAN_ID';"
        exit 0
    fi
    if [ "$STATUS" = "failed" ]; then
        echo "[5/5] FAIL — scan failed."
        docker compose exec -T postgres psql -U app -d agent_osint -tA \
            -c "SELECT error_message FROM scans WHERE id='$SCAN_ID';" >&2
        docker compose logs worker | tail -50 >&2
        exit 1
    fi
    sleep 5
done
echo "[5/5] FAIL — timeout waiting for scan." >&2
docker compose logs worker | tail -100 >&2
exit 2
