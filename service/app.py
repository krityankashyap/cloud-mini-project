"""Task 3 — command-parser inference service (FastAPI, port 8000).

Endpoints
  POST /parse   {"text": "..."} -> 200 + the four-key JSON object (body is ONLY that object).
                Response headers: X-Model-Version, X-Served-By (pod name).
                Invalid model output -> 500 {"error": "invalid_model_output"}.
  GET  /healthz -> 200 while the process is alive.
  GET  /readyz  -> 503 until the model is loaded, 200 afterwards.

Configuration (four environment variables):
  MODEL_URI        s3://<bucket>/<prefix>   e.g. s3://models/v1
  MODEL_VERSION    value returned in X-Model-Version, e.g. v1
  S3_ENDPOINT      SeaweedFS S3 endpoint, e.g. http://seaweedfs-s3.data.svc.cluster.local:8333
  MAX_NEW_TOKENS   generation budget, e.g. 64
Extra:
  FAULT_INJECT     when "1", model output is replaced with "not json" before validation.
Credentials (from a Kubernetes Secret, NOT in config/image):
  S3_ACCESS_KEY, S3_SECRET_KEY

The model + tokenizer are downloaded from object storage at start-up (never baked
into the image). Loading happens in a background thread so /readyz reports 503
until the model is ready.
"""
import json
import os
import socket
import threading

import boto3
import torch
from botocore.client import Config
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

from validation import extract_json as _extract_json, valid_output

TARGET_KEYS = ("intent", "date", "time", "place")
TEMPLATE = "Command: {command}\nJSON: "

# ---- configuration from environment ----
MODEL_URI = os.environ.get("MODEL_URI", "s3://models/v1")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "v1")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:8333")
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "64"))
FAULT_INJECT = os.environ.get("FAULT_INJECT", "0") == "1"
POD_NAME = os.environ.get("POD_NAME") or socket.gethostname()
MODEL_DIR = os.environ.get("MODEL_DIR", "/tmp/model")

# Pin torch threads to the container's CPU budget. Inside a cgroup-limited container
# torch otherwise sees ALL host cores and spawns that many threads, which thrash
# against a 1-CPU limit (observed ~20s/request vs ~1s when pinned to 1 thread).
torch.set_num_threads(int(os.environ.get("TORCH_THREADS", "1")))

# schema baked into the image (used to validate every model output)
with open(os.environ.get("SCHEMA_PATH", "schema.json"), encoding="utf-8") as _f:
    SCHEMA = json.load(_f)
VALID_INTENTS = set(SCHEMA["intents"])

app = FastAPI()
_state = {"ready": False, "model": None, "tokenizer": None}
# Serialize generation within a pod: each CPU-bound request then gets the full thread
# budget instead of N concurrent requests thrashing TORCH_THREADS*N threads on the CPU.
_gen_lock = threading.Lock()


class ParseRequest(BaseModel):
    text: str


def _download_model():
    """Download every object under MODEL_URI's prefix into MODEL_DIR, then load it."""
    assert MODEL_URI.startswith("s3://"), "MODEL_URI must be s3://bucket/prefix"
    bucket, _, prefix = MODEL_URI[len("s3://"):].partition("/")
    s3 = boto3.client(
        "s3", endpoint_url=S3_ENDPOINT,
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        config=Config(signature_version="s3v4"), region_name="us-east-1",
    )
    os.makedirs(MODEL_DIR, exist_ok=True)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel = key[len(prefix.rstrip("/") + "/"):]
            if not rel:
                continue
            dest = os.path.join(MODEL_DIR, rel)
            os.makedirs(os.path.dirname(dest) or MODEL_DIR, exist_ok=True)
            s3.download_file(bucket, key, dest)
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(MODEL_DIR)
    model.eval()
    _state["tokenizer"], _state["model"] = tok, model
    _state["ready"] = True
    print(f"[service] model {MODEL_VERSION} loaded from {MODEL_URI}", flush=True)


@app.on_event("startup")
def _startup():
    threading.Thread(target=_download_model, daemon=True).start()


def _generate(text):
    tok, model = _state["tokenizer"], _state["model"]
    ids = tok(TEMPLATE.format(command=text), return_tensors="pt")
    with _gen_lock, torch.no_grad():
        out = model.generate(**ids, max_new_tokens=MAX_NEW_TOKENS, do_sample=False,
                             pad_token_id=tok.pad_token_id, eos_token_id=tok.eos_token_id)
    return tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)


@app.get("/healthz")
def healthz():
    return Response(status_code=200)


@app.get("/readyz")
def readyz():
    return Response(status_code=200 if _state["ready"] else 503)


@app.post("/parse")
def parse(req: ParseRequest):
    headers = {"X-Model-Version": MODEL_VERSION, "X-Served-By": POD_NAME}
    if not _state["ready"]:
        return JSONResponse({"error": "model_not_ready"}, status_code=503, headers=headers)

    raw = _generate(req.text)
    if FAULT_INJECT:
        raw = "not json"  # forced invalid output, for testing the validation path

    obj = _extract_json(raw)
    if obj is None or not valid_output(obj, VALID_INTENTS):
        return JSONResponse({"error": "invalid_model_output"}, status_code=500, headers=headers)

    # body is ONLY the four-key object, in canonical key order
    clean = {k: obj[k] for k in TARGET_KEYS}
    return JSONResponse(clean, status_code=200, headers=headers)
