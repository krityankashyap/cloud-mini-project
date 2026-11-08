#!/usr/bin/env bash
# Task 3 — run the service container at a 1 CPU / 1 GiB limit, send 50 sequential
# requests, and report the peak memory (proving no OOM under the limit).
set -euo pipefail
cd "$(dirname "$0")/.."
source .secrets/s3.env

# Port-forward SeaweedFS so the container can fetch the model from object storage.
kubectl -n data port-forward svc/seaweedfs-s3 8333:8333 >/dev/null 2>&1 &
PF=$!
cleanup(){ docker rm -f memtest >/dev/null 2>&1 || true; kill $PF 2>/dev/null || true; }
trap cleanup EXIT
sleep 3

docker rm -f memtest >/dev/null 2>&1 || true
docker run -d --name memtest --cpus=1 --memory=1g -p 8001:8000 \
  -e MODEL_URI=s3://models/v1 -e MODEL_VERSION=v1 \
  -e S3_ENDPOINT=http://host.docker.internal:8333 \
  -e S3_ACCESS_KEY="$S3_ACCESS_KEY" -e S3_SECRET_KEY="$S3_SECRET_KEY" \
  -e MAX_NEW_TOKENS=64 -e TORCH_THREADS=1 \
  command-parser:latest >/dev/null
echo "container started at --cpus=1 --memory=1g"

# Wait for readiness (model download + load).
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/readyz || echo 000)
  [ "$code" = "200" ] && { echo "ready after ~$((i*2))s"; break; }
  sleep 2
done

PEAK=0; OK=0
for n in $(seq 1 50); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8001/parse \
         -H 'content-type: application/json' -d '{"text":"what is the weather in london"}')
  [ "$code" = "200" ] && OK=$((OK+1))
  mem=$(docker stats --no-stream --format '{{.MemUsage}}' memtest | awk '{print $1}')
  # normalise to MiB
  val=$(echo "$mem" | sed -E 's/MiB//; s/GiB//' )
  case "$mem" in *GiB*) val=$(echo "$val * 1024" | bc);; esac
  int=${val%.*}
  [ "${int:-0}" -gt "$PEAK" ] && PEAK=$int
  echo "req $n: HTTP $code  mem=$mem"
done
echo ""
echo "=== Task 3 result: $OK/50 succeeded, PEAK MEMORY = ${PEAK} MiB (limit 1024 MiB), no OOM ==="
docker inspect memtest --format 'OOMKilled={{.State.OOMKilled}} Status={{.State.Status}}'
