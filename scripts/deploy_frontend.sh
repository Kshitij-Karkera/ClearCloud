#!/usr/bin/env bash
set -euo pipefail

STACK="${1:-clearcloud}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

BUCKET="$(output FrontendBucketName)"
DISTRIBUTION="$(output DistributionId)"
SITE_URL="$(output SiteUrl)"

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

cp "$ROOT"/src/frontend/index.html "$ROOT"/src/frontend/style.css "$ROOT"/src/frontend/app.js "$STAGING"/
# The role ClearCloud runs as is shown on the Connect form so users can trust it from their own account.
ROLE_ARN="$(aws lambda get-function-configuration --function-name clearcloud-scanner \
  --query Role --output text 2>/dev/null || true)"
case "$ROLE_ARN" in
  arn:aws:iam::*) printf '{"trusted_role_arn": "%s"}\n' "$ROLE_ARN" > "$STAGING/config.json" ;;
  *) printf '{}\n' > "$STAGING/config.json" ;;
esac

aws s3 sync "$STAGING" "s3://$BUCKET" --delete
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths "/*" > /dev/null

echo "Published. Open: $SITE_URL"