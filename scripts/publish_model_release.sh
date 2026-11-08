#!/usr/bin/env bash
# Publish a trained model directory as a GitHub Release asset (Task 2) and record
# the asset's SHA-256 in object storage as models/<ver>/model.sha256, so that the
# checksum of the Release asset matches models/<ver>/model.sha256.
#
# Usage:
#   scripts/publish_model_release.sh <model_dir> <version> <release_tag> [asset_name]
#   scripts/publish_model_release.sh training/artifacts/v1 v1 model-v1 pythia70m-student-v1.tar.gz
#
# Requires: gh (authenticated), and (to update the checksum in object storage) a
# running SeaweedFS port-forward with S3_* env vars sourced from .secrets/s3.env.
set -euo pipefail

DIR="${1:?model dir}"; VER="${2:?version}"; TAG="${3:?release tag}"
ASSET="${4:-pythia70m-student-${VER}.tar.gz}"
OUT="/tmp/${ASSET}"

# Deterministic-ish tarball of the model files (sorted names, no gzip timestamp).
( cd "$DIR" && tar cf - $(ls -1 | sort) ) | gzip -n > "$OUT"
DIGEST=$(shasum -a 256 "$OUT" | awk '{print $1}')
echo "asset   : $OUT ($(du -h "$OUT" | awk '{print $1}'))"
echo "sha256  : $DIGEST"

# Record the asset checksum as models/<ver>/model.sha256 (requires S3 port-forward).
if [ -n "${S3_ENDPOINT:-}" ] && [ -n "${S3_ACCESS_KEY:-}" ]; then
  python3 - "$VER" "$DIGEST" "$ASSET" <<'PY' || echo "(skipped S3 checksum update)"
import os, sys, boto3
from botocore.client import Config
ver, digest, asset = sys.argv[1], sys.argv[2], sys.argv[3]
s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],
                  aws_access_key_id=os.environ["S3_ACCESS_KEY"],
                  aws_secret_access_key=os.environ["S3_SECRET_KEY"],
                  config=Config(signature_version="s3v4"), region_name="us-east-1")
body = f"{digest}  {asset}\n".encode()
s3.put_object(Bucket="models", Key=f"{ver}/model.sha256", Body=body)
print(f"updated s3://models/{ver}/model.sha256 = {digest}")
PY
fi

# Create or update the release and upload the asset.
if gh release view "$TAG" >/dev/null 2>&1; then
  gh release upload "$TAG" "$OUT" --clobber
else
  gh release create "$TAG" "$OUT" \
    --title "Student model ${VER}" \
    --notes "EleutherAI/pythia-70m fine-tuned (sequence-level distillation). Asset SHA-256: ${DIGEST}"
fi
echo "published $ASSET to release $TAG"
