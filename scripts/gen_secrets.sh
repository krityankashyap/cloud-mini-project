#!/usr/bin/env bash
# Create the Kubernetes Secrets used by SeaweedFS, the workers and the service.
# Credentials are generated locally (or taken from $S3_ACCESS_KEY / $S3_SECRET_KEY)
# and NEVER committed to git. Safe to re-run (idempotent via apply).
set -euo pipefail

S3_ACCESS_KEY="${S3_ACCESS_KEY:-$(openssl rand -hex 12)}"
S3_SECRET_KEY="${S3_SECRET_KEY:-$(openssl rand -hex 24)}"

# Persist the generated creds locally (gitignored) so other make targets reuse them.
mkdir -p .secrets
cat > .secrets/s3.env <<EOF
export S3_ACCESS_KEY=${S3_ACCESS_KEY}
export S3_SECRET_KEY=${S3_SECRET_KEY}
EOF

# SeaweedFS S3 identity config.
S3_CONFIG_JSON=$(cat <<EOF
{
  "identities": [
    {
      "name": "admin",
      "credentials": [{"accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}"}],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
EOF
)

for ns in data pipeline app; do
  kubectl get namespace "$ns" >/dev/null 2>&1 || kubectl create namespace "$ns"
  kubectl -n "$ns" create secret generic s3-credentials \
    --from-literal=S3_ACCESS_KEY="${S3_ACCESS_KEY}" \
    --from-literal=S3_SECRET_KEY="${S3_SECRET_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -
done

# SeaweedFS identity config secret (only needed in the data namespace).
kubectl -n data create secret generic seaweedfs-s3-config \
  --from-literal=s3.json="${S3_CONFIG_JSON}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Secrets created. Credentials saved to .secrets/s3.env (gitignored)."
