# Cloud Computing Mini Project — orchestration.
#
# Quick start (fresh clone, <= 3 commands):
#   make up            # create cluster, build images, deploy storage + service
#   make down          # delete the cluster
#
# Training:
#   make train             # train student v1 (first 1500 records) on CPU
#   make train SUBSET=50   # quick smoke test on the first 50 records
#   make train-v2          # train v2 (different seed + extra epoch) for Task 4

SHELL := /bin/bash
CLUSTER      := cloud-mini
PYTHON       ?= python3
VENV         := .venv
VENV_PY      := $(VENV)/bin/python
# TRAIN_PY: interpreter used to build the training venv (needs Python 3.11)
TRAIN_PY     ?= python3.11
# TOOLVENV: host-side venv for the S3 helper scripts (boto3)
TOOLVENV     := .toolvenv
TOOL_PY      := $(TOOLVENV)/bin/python
SUBSET       ?=
NS_DATA      := data

.PHONY: help up down cluster cluster-delete images secrets storage wait-storage \
        buckets deploy-app label clean-data venv toolvenv train train-v2 \
        upload-model seed-model predict-student predict-untrained score-all nacl test lint

help:
	@grep -E '^[a-zA-Z0-9_-]+:.*?#' $(MAKEFILE_LIST) | sed 's/:.*#/\t-/' | sort

# ---------------------------------------------------------------------------
# Cluster + deployment (Task 1 / Task 4)
# ---------------------------------------------------------------------------
up: cluster images secrets storage wait-storage buckets deploy-app   ## Build + deploy the whole system
	@echo ""
	@echo "System deployed. Now seed the models so the service becomes Ready:  make seed-model"
	@echo "Then test:  curl -s -X POST localhost:8080/parse -d '{\"text\":\"what is the weather in london\"}'"

down: cluster-delete   ## Delete the k3d cluster

cluster:   ## Create the k3d cluster from cluster/k3d.yaml
	@k3d cluster list | grep -q '^$(CLUSTER)' || k3d cluster create --config cluster/k3d.yaml

cluster-delete:
	@k3d cluster delete $(CLUSTER) || true

images:   ## Build service + worker images and import them into k3d
	docker build -t command-parser:latest -f service/Dockerfile .
	docker build -t teacher-worker:latest -f worker/Dockerfile .
	k3d image import command-parser:latest teacher-worker:latest -c $(CLUSTER)

secrets:   ## Create S3 credential + config Secrets (generated, gitignored)
	bash scripts/gen_secrets.sh

storage:   ## Deploy SeaweedFS in namespace data
	kubectl apply -f k8s/data/seaweedfs.yaml

wait-storage:
	kubectl -n $(NS_DATA) rollout status deploy/seaweedfs --timeout=180s

toolvenv:   ## Create the host-side venv (boto3) used by the S3 helper scripts
	@test -d $(TOOLVENV) || $(PYTHON) -m venv $(TOOLVENV)
	@$(TOOL_PY) -m pip install -q --upgrade pip
	@$(TOOL_PY) -m pip install -q boto3==1.34.162

# Run a command with a background port-forward to the SeaweedFS S3 port.
# usage: $(call with_s3, <commands>)
define with_s3
	set -euo pipefail; source .secrets/s3.env; \
	kubectl -n $(NS_DATA) port-forward svc/seaweedfs-s3 8333:8333 >/dev/null 2>&1 & \
	PF=$$!; trap "kill $$PF 2>/dev/null || true" EXIT; sleep 3; \
	export S3_ENDPOINT=http://localhost:8333; \
	$(1)
endef

buckets: toolvenv   ## Create datasets + models buckets and upload commands_pool.jsonl
	@$(call with_s3, \
	  $(TOOL_PY) scripts/storage.py create-buckets datasets models; \
	  $(TOOL_PY) scripts/storage.py upload-file data/commands_pool.jsonl datasets commands_pool.jsonl)

upload-model: toolvenv   ## Upload a local model dir to object storage. usage: make upload-model DIR=training/artifacts/v1 VER=v1
	@test -n "$(DIR)" || (echo "set DIR=training/artifacts/v1"; exit 1)
	@$(call with_s3, $(TOOL_PY) scripts/storage.py upload-dir $(DIR) models $(or $(VER),v1))

seed-model: toolvenv   ## Fresh-clone deploy: fetch released v1/v2 models from GitHub Releases into object storage
	@set -e; for v in v1 v2; do \
	  asset=pythia70m-student-$$v.tar.gz; \
	  rm -rf /tmp/seed_$$v /tmp/$$asset && mkdir -p /tmp/seed_$$v; \
	  gh release download model-$$v --pattern $$asset --dir /tmp --clobber \
	    || { echo "ERROR: could not download $$asset from release model-$$v"; exit 1; }; \
	  tar -xzf /tmp/$$asset -C /tmp/seed_$$v; \
	done
	@$(call with_s3, \
	  $(TOOL_PY) scripts/storage.py upload-dir /tmp/seed_v1 models v1 --no-sha; \
	  $(TOOL_PY) scripts/storage.py write-sha256 /tmp/pythia70m-student-v1.tar.gz models v1/model.sha256; \
	  $(TOOL_PY) scripts/storage.py upload-dir /tmp/seed_v2 models v2 --no-sha; \
	  $(TOOL_PY) scripts/storage.py write-sha256 /tmp/pythia70m-student-v2.tar.gz models v2/model.sha256)
	kubectl -n app rollout restart deploy/command-parser 2>/dev/null || true

deploy-app:   ## Deploy the service: Deployment, Service, Ingress, HPA
	kubectl apply -f k8s/app/deployment.yaml
	kubectl apply -f k8s/app/service.yaml
	kubectl apply -f k8s/app/ingress.yaml
	kubectl apply -f k8s/app/hpa.yaml

label:   ## Run the two teacher-labelling worker Jobs (Task 1)
	kubectl apply -f k8s/pipeline/workers.yaml

clean-data: toolvenv   ## Run clean.py against the shard outputs in object storage
	@$(call with_s3, $(TOOL_PY) worker/clean.py)

# ---------------------------------------------------------------------------
# Training (Task 2)
# ---------------------------------------------------------------------------
venv:   ## Create the training virtualenv (Python 3.11) with pinned deps
	@test -d $(VENV) || $(TRAIN_PY) -m venv $(VENV)
	$(VENV_PY) -m pip install -q --upgrade pip
	$(VENV_PY) -m pip install -q -r training/requirements.txt

train: venv   ## Train student v1 (make train SUBSET=50 for a quick smoke test)
	$(VENV_PY) training/train.py --version v1 $(if $(SUBSET),--subset $(SUBSET),)

train-v2: venv   ## Train student v2 (different seed + extra epoch)
	$(VENV_PY) training/train.py --version v2 $(if $(SUBSET),--subset $(SUBSET),)

predict-student: venv   ## Student predictions on validation.jsonl
	$(VENV_PY) training/predict.py --model training/artifacts/v1 --out results/student_v1_preds.jsonl

predict-untrained: venv   ## Untrained Pythia-70M predictions (for the quality-gate demo)
	$(VENV_PY) training/predict.py --model EleutherAI/pythia-70m --out results/untrained_preds.jsonl

score-all: venv   ## Score student + untrained against validation
	$(VENV_PY) tools/score.py results/student_v1_preds.jsonl data/validation.jsonl --out results/student_v1_score.json
	$(VENV_PY) tools/score.py results/untrained_preds.jsonl data/validation.jsonl --out results/untrained_score.json

# ---------------------------------------------------------------------------
# Tests / lint / network (Task 5, Task 6)
# ---------------------------------------------------------------------------
nacl:   ## Check the NACL against the sample packets
	$(PYTHON) tools/nacl_sim.py network/nacl.json network/packets_sample.jsonl --check

test:   ## Run unit tests
	$(PYTHON) -m pytest -q tests/

lint:   ## Lint the Python sources
	$(PYTHON) -m ruff check . || true
