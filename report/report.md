# Cloud Computing Mini Project — Report

> Draft to be exported to `report/report.pdf` (≤ 8 pages). One section per task.
> **Measured results** (filled from `results/`) are kept separate from **assumptions**.
> Replace every `MEASURED:` placeholder with the real number from your run.

**Name / Roll number:** ______   **GitHub:** ______

---

## Architecture overview
Teacher **phi3.5** (Ollama, temperature 0, seed 42) labels 3,000 commands in
parallel shards; replies are cleaned into `train.jsonl` and used as sequence-level
distillation targets to fine-tune **pythia-70m**. The student is stored in
**SeaweedFS** object storage and served by a **FastAPI** container on a local
**k3d** cluster behind a **Traefik** Ingress, with an HPA, probes, and CI/CD.

## Task 1 — Object storage & parallel teacher labelling
- **Sharding.** Command at line `L` (from 0) → shard `L % SHARD_COUNT`. Two workers
  (SHARD_INDEX 0,1; SHARD_COUNT 2) write `datasets/teacher/shard-{0,1}.jsonl`. This
  guarantees a disjoint, exhaustive partition (unit test `test_sharding...`).
- **Cleaning counts** (from `make clean-data`, full 3,000):

  | input | (a) invalid JSON | (b) wrong keys | (c) bad intent | (d) bad slot | duplicates | kept |
  |-------|------------------|----------------|----------------|--------------|------------|------|
  | 3000 | 6 | 2 | 11 | 61 | 49 | 2871 |

  Reconciles: `3000 = 6+2+11+61+49+2871`. `train.sha256` =
  `c5d6c2507849a0649f1bc6fc0d0de681e881e8dcd285a96707d14d7b88004f1e`
  (reproducible offline: `python worker/clean.py --local-shards datasets/teacher ...`).
  Labelling ran as two in-cluster Jobs (SHARD_INDEX 0/1, SHARD_COUNT 2), 108 min total.

## Task 2 — Training the student
- Config: seed 42, lr 5e-4, batch 8, **epochs 6**, max_seq_len 128 (`training/config.yaml`).
  (3 epochs gave only 89% JSON validity; 6 epochs reaches 99%.)
- Loss per epoch (`training/loss.csv`): 1.97, 0.42, 0.31, 0.32, 0.24, 0.20.
- **Three-model comparison** (score.py on `validation.jsonl`, in `results/`):

  | Model | json_validity | intent_acc | date | time | place | full_frame |
  |-------|---------------|------------|------|------|-------|------------|
  | Teacher (phi3.5) | 100 | 85 | 77 | 84 | 85 | 45 |
  | Untrained pythia-70m | 0 | 0 | 0 | 0 | 0 | 0 |
  | Student v1 (6 epochs) | 99 | 78 | 65 | 67 | 75 | 24 |

  Student full-frame 24% ≥ half of teacher's 45% (22.5%); validity 99% ≥ 95%.
  Distillation recovers 78/85 = 92% of the teacher's intent accuracy with a 70M model.

- **Sequence-level vs logit-based distillation.** (a) Sequence-level trains on the
  teacher's *generated output text* (hard targets) — standard SFT on
  `Command → JSON`. (b) Logit-based (KD) trains the student to match the teacher's
  *full next-token probability distribution* (soft targets) via KL divergence.
  (c) Not possible here because we only stored the teacher's text, not its logits,
  and phi3.5 (Ollama) does not expose per-token distributions; vocabularies also
  differ. (d) Limitation: a 70M model has little capacity, so it memorises surface
  patterns and generalises poorly on unseen phrasings.

## Task 3 — Inference service
- Endpoints `/parse`, `/healthz`, `/readyz`; headers `X-Model-Version`, `X-Served-By`.
- `FAULT_INJECT=1` → HTTP 500 `{"error":"invalid_model_output"}` (verified).
- Image size: **1.2 GB** (CPU-only torch, no weights; ≤ 2 GB). Peak memory @ 1 CPU / 1 GiB
  over 50 sequential requests: MEASURED MiB (evidence in `results/`).
- Verified live through the Ingress: `what is the weather in london` →
  `{"intent":"weather_query","date":null,"time":null,"place":"london"}` with headers
  `X-Model-Version: v1`, `X-Served-By: <pod>`.

## Task 4 — Kubernetes deployment, scaling, reliability
- Ingress `/parse` answers; requests/limits set; no ready endpoints during model load.
- HPA 1→2 under load, back to 1 (evidence MEASURED).
- Rolling update v1→v2 with ≤0.5% failures; rollback restores `X-Model-Version: v1`.
- Recovery: deleted pod restored; failures MEASURED.
- Cold start (3 runs): MEASURED / MEASURED / MEASURED → median MEASURED s.
- Load balancing (2 replicas): MEASURED % / MEASURED % per replica.
- **Limitation:** single laptop = single node; no cross-machine fault tolerance.

## Task 5 — Subnetting & NACL  *(computed)*

| Subnet | Network | Prefix | Usable range | Broadcast | Usable |
|--------|---------|--------|--------------|-----------|--------|
| app | 10.20.0.0 | /22 | 10.20.0.1–10.20.3.254 | 10.20.3.255 | 1022 |
| public | 10.20.4.0 | /24 | 10.20.4.1–10.20.4.254 | 10.20.4.255 | 254 |
| data | 10.20.5.0 | /25 | 10.20.5.1–10.20.5.126 | 10.20.5.127 | 126 |
| management | 10.20.5.128 | /27 | 10.20.5.129–10.20.5.158 | 10.20.5.159 | 30 |

NACL verified 6/6 on sample packets. A stateless NACL needs explicit reply rules
(it keeps no connection state), and the `198.51.100.0/24` deny must have a lower
rule number (100) than the allow-80/443 rules so it is matched first. Full working
in `network/subnets.md`.

## Task 6 — Reproducibility, testing, CI/CD
- `make up` (≤3 commands) / `make down`. CI stages: lint → tests (≥3) → eval from
  Release → quality gate `--min-validity 95`; gitleaks reports nothing.
- Failed-gate demonstration (untrained model): run link MEASURED.

## Task 7 — Performance, cost, review
- p50 MEASURED ms, p95 MEASURED ms, throughput MEASURED rps, error rate MEASURED %,
  per-pod CPU/mem MEASURED, cold start MEASURED s (Locust files in `results/`).
- **Cost (rate_card.json: ₹3.50/vCPU-h, ₹0.45/GiB-h; billed on limits).**
  Per replica/hour = 1 vCPU × 3.50 + 1 GiB × 0.45 = **₹3.95**. With N running
  replicas and R requests/hour: `cost_per_1000 = (N × 3.95 / R) × 1000`. With
  measured R = MEASURED and N = MEASURED → ₹ MEASURED per 1,000 requests.
- **SLO:** 99% of requests succeed over one 2-minute load-test window. Error budget
  = 1% × (total requests MEASURED) = MEASURED requests; used MEASURED.
- **Review table:**

  | Concern | Evidence (measured) | Limitation | Improvement |
  |---------|---------------------|------------|-------------|
  | Data and storage | MEASURED | | |
  | Model quality | MEASURED | | |
  | Latency and throughput | MEASURED | | |
  | Resource utilisation | MEASURED | | |
  | Reliability and recovery | MEASURED | | |
  | Security and cost | MEASURED | | |
