# AI Use

This project was developed with AI assistance, as the handout explicitly permits.
This file records which tools were used, for what, and how the output was checked.

## Tools used

- **Claude (Anthropic), via Claude Code** — scaffolding the repository: the sharded
  worker and `clean.py`, the training/prediction scripts, the FastAPI service,
  Dockerfiles, Kubernetes manifests, the Makefile, the subnet/NACL files, unit
  tests, and the CI workflow. Also used for explanations of knowledge distillation
  and for drafting the report.

## What it was used for

| Area | How AI helped |
|------|---------------|
| Task 1 | Shard-partition logic, resumable worker, `clean.py` rejection rules (a)–(e) |
| Task 2 | Manual CPU training loop with prompt masking, config/loss plumbing |
| Task 3 | FastAPI service, background model load, schema validation, fault injection |
| Task 4 | Deployment/Service/Ingress/HPA manifests, probe tuning |
| Task 5 | Subnet arithmetic and the stateless NACL rules + ordering explanation |
| Task 6 | Makefile targets, pytest tests, GitHub Actions stages |

## How the output was checked

- **Executed, not trusted.** `clean.py` was run on synthetic shards to confirm the
  reconciliation identity `input = (a)+(b)+(c)+(d)+duplicates+kept` and a
  deterministic checksum across reruns. The NACL was verified with
  `nacl_sim.py` against the sample packets (6/6). Unit tests (`pytest`) pass.
- **Subnet table recomputed by hand** using `usable = 2^(32-prefix) - 2` and
  cross-checked with `ipaddress`.
- **Measured results are real.** All score.py / Locust / `kubectl top` numbers in the
  report come from actual runs and are kept in `results/`. The student model is
  small and makes errors; no results were fabricated or edited.
- **Prompts and generated data** were reviewed; teacher replies are stored exactly
  as produced (no manual editing), per the project rules.

## Limitations I remained aware of

AI-generated manifests and scripts were reviewed for correctness (secret handling,
resource limits, probe behaviour) rather than accepted blindly; where the AI's first
suggestion was wrong or wasteful (e.g. GPU PyTorch wheels inflating the image), it
was corrected.
