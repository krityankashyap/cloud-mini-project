# Cloud Computing Mini Project — Command-Parser Distillation & Deployment

Sequence-level knowledge distillation of a command-parsing LLM (teacher **phi3.5**
→ student **EleutherAI/pythia-70m**), deployed as an inference service on a local
**k3d** Kubernetes cluster with **SeaweedFS** object storage.

## Prerequisites

| Tool | Install (macOS) | Used for |
|------|-----------------|----------|
| Docker (running) | Docker Desktop | building images, k3d runtime |
| k3d | `brew install k3d` | local Kubernetes cluster |
| kubectl | `brew install kubectl` | cluster control |
| Ollama + phi3.5 | download from ollama.com, then `ollama pull phi3.5` | teacher labelling (Task 1) |
| Python 3.11 | `brew install python@3.11` | training venv (PyTorch has no 3.14 wheels) |
| Locust | `pip install locust==2.31.6` | load testing (Tasks 4, 7) |
| gitleaks (optional, local) | `brew install gitleaks` | secret scan |

> The machine here has Python 3.14 as `python3`; the training Makefile builds its
> venv with `python3.11` (`TRAIN_PY` override). All long jobs (teacher labelling,
> training) run on CPU and take time — start Task 1 early.

## Build, deploy, test, tear down

```bash
# 1. Deploy the whole system (cluster + object storage + service)
make up

# 2. Seed the models from the GitHub Releases so the service becomes Ready
#    (fresh clone has no local weights; this fetches model-v1 / model-v2 into object storage)
make seed-model

# 3. Tear everything down
make down
```

> Three commands from a fresh clone. (If you trained locally instead, use
> `make upload-model DIR=training/artifacts/v1 VER=v1` in place of `make seed-model`.)

`make up` = create k3d cluster → build & import images → create Secrets →
deploy SeaweedFS → create `datasets`/`models` buckets + upload `commands_pool.jsonl`
→ deploy Deployment/Service/Ingress/HPA. The service reaches Ready once a model
exists under `models/v1` in object storage.

Smoke-test the service through the Ingress:

```bash
curl -s -X POST localhost:8080/parse \
  -H 'content-type: application/json' \
  -d '{"text":"what is the weather in london"}' -i
```

## End-to-end workflow (by task)

### Task 1 — Object storage + parallel teacher labelling
```bash
ollama serve &                       # teacher on the host (phi3.5, temperature 0, seed 42)
make up                              # storage up, commands_pool.jsonl in datasets bucket
make label                           # two worker Jobs (SHARD_INDEX 0 and 1) in namespace pipeline
kubectl -n pipeline logs -f job/teacher-shard-0
make clean-data                      # worker/clean.py -> datasets/v1/train.jsonl (+ train.sha256)
```
Sharding: a command at line `L` (from 0) belongs to shard `L % SHARD_COUNT`, so
every id lands in exactly one shard (no gaps / no overlaps). See `prompts/teacher_prompt.txt`.

### Task 2 — Train the student (sequence-level distillation)
```bash
# fetch train.jsonl locally first (clean.py can also write locally):
#   kubectl -n data port-forward svc/seaweedfs-s3 8333:8333 &
#   source .secrets/s3.env; S3_ENDPOINT=http://localhost:8333 \
#     python worker/clean.py            # writes to object storage
make train SUBSET=50                  # quick check that training runs
make train                            # student v1 (first 1500 records) -> training/artifacts/v1
make train-v2                         # student v2 (seed 123, +1 epoch) -> training/artifacts/v2
make upload-model DIR=training/artifacts/v1 VER=v1   # -> models/v1 (+ model.sha256)
make upload-model DIR=training/artifacts/v2 VER=v2   # -> models/v2
```
Config (seed, lr, batch, epochs, max_seq_len, library versions) is in
`training/config.yaml`; per-epoch loss is written to `training/loss.csv`.

Evaluation (three models, Task 2 step 6):
```bash
python scripts/teacher_predict.py --out results/teacher_preds.jsonl      # teacher (Ollama)
python tools/score.py results/teacher_preds.jsonl data/validation.jsonl --out results/teacher_validation.json
make predict-untrained predict-student score-all                          # untrained + student
```

### Task 3 — Inference service container
```bash
docker build -t command-parser:latest -f service/Dockerfile .            # one command, from a fresh clone
docker run --rm -p 8000:8000 \
  -e MODEL_URI=s3://models/v1 -e MODEL_VERSION=v1 \
  -e S3_ENDPOINT=http://host.docker.internal:8333 \
  -e S3_ACCESS_KEY=... -e S3_SECRET_KEY=... -e MAX_NEW_TOKENS=64 \
  command-parser:latest
# FAULT_INJECT=1 makes /parse return HTTP 500 {"error":"invalid_model_output"}
```

### Task 4 — Kubernetes deployment, scaling, reliability
Deployment/Service/Ingress/HPA are in `k8s/app/`. See `report/report.md` for the
rolling-update, recovery, cold-start and load-balancing procedures.

### Task 5 — Subnetting + NACL
`network/subnets.md` (allocation table + working) and `network/nacl.json`.
```bash
make nacl        # python tools/nacl_sim.py network/nacl.json network/packets_sample.jsonl --check
```

### Task 6 — Reproducibility, tests, CI/CD
```bash
make test        # unit tests (pytest)
make lint        # ruff
```
GitHub Actions: `.github/workflows/ci.yml` (lint → tests → model eval from Release →
quality gate `--min-validity 95`, plus gitleaks). See `AI_USE.md`.

### Task 7 — Performance, cost, review
```bash
locust -f tools/locustfile.py --headless --users 4 --spawn-rate 4 --run-time 2m \
  --host http://localhost:8080 --csv results/loadtest
kubectl top pods -n app
```

## Repository layout
```
cluster/    k3d cluster config            service/   FastAPI inference service + Dockerfile
prompts/    teacher prompt                k8s/       data / pipeline / app manifests
worker/     shard worker + clean.py       network/   subnets.md + nacl.json
training/   config, train, predict        tools/     score.py, locustfile.py, nacl_sim.py (kit)
scripts/    storage + secret helpers      tests/     unit tests
data/       kit data + schema + rate card results/   evidence (logs, scores, loadtest)
```

Credentials are generated at deploy time into `.secrets/` (gitignored). Model
weights live in object storage / a GitHub Release — never in git or the image.
