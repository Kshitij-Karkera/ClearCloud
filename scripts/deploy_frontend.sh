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
printf '{}\n' > "$STAGING/config.json"

aws s3 sync "$STAGING" "s3://$BUCKET" --delete
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths "/*" > /dev/null

echo "Published. Open: $SITE_URL"
