#!/usr/bin/env bash
set -euo pipefail

STACK="${1:-clearcloud}"
ROLE_ARN="${2:-}"
ADMIN_EMAILS="${3:-}"
if [ -z "$ROLE_ARN" ]; then
  echo "usage: ./scripts/deploy_backend.sh <stack-name> <existing-lambda-role-arn> [admin-email]" >&2
  exit 1
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -z "${AWS_ACCESS_KEY_ID:-}" ]; then
  eval "$(aws configure export-credentials --format env)"
fi
EXPIRY="$(aws configure export-credentials --format process 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("Expiration","unknown"))' 2>/dev/null || echo unknown)"
echo "Session credentials expire: $EXPIRY"
if [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  echo "Could not read session credentials" >&2
  exit 1
fi

# The token-signing secret is created once and reused, so redeploys do not sign users out.
JWT_FILE="$HOME/.clearcloud_jwt_secret"
if [ ! -s "$JWT_FILE" ]; then
  umask 077
  openssl rand -hex 32 > "$JWT_FILE"
fi
JWT_SECRET="$(cat "$JWT_FILE")"

# A stack that failed its first create is stuck in ROLLBACK_COMPLETE and must be removed first.
STATUS="$(aws cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo NONE)"
if [ "$STATUS" = "ROLLBACK_COMPLETE" ]; then
  echo "Removing failed stack $STACK ..."
  aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK"
fi

sam build
sam deploy \
  --stack-name "$STACK" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "LabRoleArn=$ROLE_ARN" \
    "JwtSecret=$JWT_SECRET" \
    "AdminEmails=$ADMIN_EMAILS" \
    "AwsAccessKeyId=$AWS_ACCESS_KEY_ID" \
    "AwsSecretAccessKey=$AWS_SECRET_ACCESS_KEY" \
    "AwsSessionToken=${AWS_SESSION_TOKEN:-}"

echo "Backend deployed. Session credentials expire; run this script again to refresh them."
