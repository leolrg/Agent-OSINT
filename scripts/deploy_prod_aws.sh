#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

AWS_REGION="${AWS_REGION:-us-east-1}"
PROJECT_NAME="${PROJECT_NAME:-agent-osint}"
ENVIRONMENT_NAME="${ENVIRONMENT_NAME:-prod}"
STACK_NAME="${STACK_NAME:-AgentOsintProdStack}"
SKIP_BOOTSTRAP="${SKIP_BOOTSTRAP:-0}"
SKIP_IMAGES="${SKIP_IMAGES:-0}"
SKIP_DEPLOY="${SKIP_DEPLOY:-0}"
SKIP_MIGRATE="${SKIP_MIGRATE:-0}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
ASSUME_YES=0

usage() {
  cat <<EOF
Usage: $0 [--yes]

Deploys Agent OSINT production infrastructure and images to AWS.

Environment overrides:
  AWS_REGION           default: us-east-1
  PROJECT_NAME         default: agent-osint
  ENVIRONMENT_NAME     default: prod
  STACK_NAME           default: AgentOsintProdStack
  IMAGE_TAG            default: <git-sha>-<timestamp>
  DOCKER_PLATFORM      default: linux/amd64
  SKIP_BOOTSTRAP=1     skip cdk bootstrap
  SKIP_IMAGES=1        skip Docker build/push and reuse IMAGE_TAG
  SKIP_DEPLOY=1        skip CDK deploy after pushing images
  SKIP_MIGRATE=1       skip ECS Drizzle database migration task
  SKIP_SMOKE=1         skip ALB /healthz smoke test
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes)
      ASSUME_YES=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $1" >&2
    exit 1
  fi
}

require_cmd aws
require_cmd npm
require_cmd npx
require_cmd git
require_cmd curl

if [ "$SKIP_IMAGES" != "1" ]; then
  require_cmd docker
  docker info >/dev/null
fi

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
AWS_ARN="$(aws sts get-caller-identity --query Arn --output text)"
REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)}"

echo "AWS account:   ${AWS_ACCOUNT_ID}"
echo "AWS identity:  ${AWS_ARN}"
echo "AWS region:    ${AWS_REGION}"
echo "Stack:         ${STACK_NAME}"
echo "Image tag:     ${IMAGE_TAG}"
echo "Platform:      ${DOCKER_PLATFORM}"
echo

if [ "$ASSUME_YES" -ne 1 ]; then
  echo "This will create or update billable AWS resources."
  printf "Type 'deploy' to continue: "
  read -r answer
  if [ "$answer" != "deploy" ]; then
    echo "Aborted."
    exit 1
  fi
fi

echo "[1/8] Logging in to ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

echo "[2/8] Ensuring ECR repositories..."
for repo in \
  "${PROJECT_NAME}-${ENVIRONMENT_NAME}-web-next" \
  "${PROJECT_NAME}-${ENVIRONMENT_NAME}-api-py" \
  "${PROJECT_NAME}-${ENVIRONMENT_NAME}-worker-py"; do
  aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$repo" >/dev/null 2>&1 \
    || aws ecr create-repository \
      --region "$AWS_REGION" \
      --repository-name "$repo" \
      --image-scanning-configuration scanOnPush=true >/dev/null
done

WEB_IMAGE="${REGISTRY}/${PROJECT_NAME}-${ENVIRONMENT_NAME}-web-next:${IMAGE_TAG}"
API_IMAGE="${REGISTRY}/${PROJECT_NAME}-${ENVIRONMENT_NAME}-api-py:${IMAGE_TAG}"
WORKER_IMAGE="${REGISTRY}/${PROJECT_NAME}-${ENVIRONMENT_NAME}-worker-py:${IMAGE_TAG}"

if [ "$SKIP_IMAGES" != "1" ]; then
  echo "[3/8] Building Docker images..."
  docker build --platform "$DOCKER_PLATFORM" -t "$WEB_IMAGE" web-next
  docker build --platform "$DOCKER_PLATFORM" -t "$API_IMAGE" -f infra/docker/api/Dockerfile .
  docker build --platform "$DOCKER_PLATFORM" -t "$WORKER_IMAGE" -f infra/docker/worker/Dockerfile .

  echo "[4/8] Pushing Docker images..."
  docker push "$WEB_IMAGE"
  docker push "$API_IMAGE"
  docker push "$WORKER_IMAGE"
else
  echo "[3/8] Skipping Docker image build."
  echo "[4/8] Skipping Docker image push."
fi

echo "[5/8] Installing CDK dependencies..."
npm --prefix infra/cdk ci

if [ "$SKIP_DEPLOY" = "1" ]; then
  echo "[6/8] Skipping CDK bootstrap."
  echo "[7/8] Skipping CDK deploy."
  echo "[8/8] Skipping database migrations and smoke test."
  echo "Images pushed for tag ${IMAGE_TAG}."
  exit 0
fi

if [ "$SKIP_BOOTSTRAP" != "1" ]; then
  echo "[6/8] Bootstrapping CDK..."
  (cd infra/cdk && npx cdk bootstrap "aws://${AWS_ACCOUNT_ID}/${AWS_REGION}")
else
  echo "[6/8] Skipping CDK bootstrap."
fi

echo "[7/8] Deploying CDK stack..."
(cd infra/cdk && npx cdk deploy "$STACK_NAME" \
    -c projectName="$PROJECT_NAME" \
    -c environmentName="$ENVIRONMENT_NAME" \
    -c webImageTag="$IMAGE_TAG" \
    -c apiImageTag="$IMAGE_TAG" \
    -c workerImageTag="$IMAGE_TAG" \
    --require-approval never)

stack_output() {
  aws cloudformation describe-stacks \
    --region "$AWS_REGION" \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" \
    --output text
}

ALB_URL="$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AlbUrl'].OutputValue" \
  --output text)"

echo
echo "ALB URL: ${ALB_URL}"

if [ "$SKIP_MIGRATE" != "1" ]; then
  echo "[8/8] Running database migrations in ECS..."
  TASK_SECURITY_GROUP_ID="$(stack_output TaskSecurityGroupId)"
  PRIVATE_SUBNET_IDS="$(stack_output PrivateSubnetIds)"
  MIGRATION_TASK_ARN="$(aws ecs run-task \
    --region "$AWS_REGION" \
    --cluster "${PROJECT_NAME}-${ENVIRONMENT_NAME}" \
    --launch-type FARGATE \
    --task-definition "${PROJECT_NAME}-${ENVIRONMENT_NAME}-web-next" \
    --network-configuration "awsvpcConfiguration={subnets=[${PRIVATE_SUBNET_IDS}],securityGroups=[${TASK_SECURITY_GROUP_ID}],assignPublicIp=DISABLED}" \
    --overrides '{"containerOverrides":[{"name":"web-next","command":["sh","-c","export DATABASE_URL_NODE=\"postgresql://${DATABASE_USER}:${DATABASE_PASSWORD}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}?sslmode=require\"; npx drizzle-kit migrate --config drizzle.config.ts"]}]}' \
    --query 'tasks[0].taskArn' \
    --output text)"
  echo "Migration task: ${MIGRATION_TASK_ARN}"
  aws ecs wait tasks-stopped \
    --region "$AWS_REGION" \
    --cluster "${PROJECT_NAME}-${ENVIRONMENT_NAME}" \
    --tasks "$MIGRATION_TASK_ARN"
  MIGRATION_EXIT_CODE="$(aws ecs describe-tasks \
    --region "$AWS_REGION" \
    --cluster "${PROJECT_NAME}-${ENVIRONMENT_NAME}" \
    --tasks "$MIGRATION_TASK_ARN" \
    --query 'tasks[0].containers[?name==`web-next`].exitCode | [0]' \
    --output text)"
  if [ "$MIGRATION_EXIT_CODE" != "0" ]; then
    echo "ERROR: migration task exited with ${MIGRATION_EXIT_CODE}" >&2
    exit 1
  fi
else
  echo "[8/8] Skipping database migrations."
fi

if [ "$SKIP_SMOKE" != "1" ]; then
  echo "Smoke testing ${ALB_URL}/healthz..."
  curl --fail --retry 12 --retry-delay 10 "${ALB_URL}/healthz"
  echo
fi

echo "Deploy complete."
echo "If worker scans need external tools, update Secrets Manager secret ${PROJECT_NAME}/${ENVIRONMENT_NAME}/secrets with real API keys and restart the ECS services."
