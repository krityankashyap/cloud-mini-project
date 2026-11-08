"""Teacher-labelling worker (Task 1).

Reads commands_pool.jsonl from object storage, keeps only the commands belonging
to this shard, asks the Ollama teacher (phi3.5) for the four-key JSON, and writes
one object per shard back to object storage:
    datasets/teacher/shard-<SHARD_INDEX>.jsonl
Each output line is {"id": ..., "text": ..., "teacher_raw": "<exact teacher reply>"}.

Sharding (deterministic, no gaps / no overlaps):
    a command at line number L (counting from 0) belongs to this worker iff
    L % SHARD_COUNT == SHARD_INDEX.

The teacher's reply is stored EXACTLY as generated (no manual editing).

Environment variables
    SHARD_INDEX, SHARD_COUNT          this worker's shard (SHARD_INDEX in 0..SHARD_COUNT-1)
    S3_ENDPOINT                        e.g. http://seaweedfs-s3.data.svc.cluster.local:8333
    S3_ACCESS_KEY, S3_SECRET_KEY       SeaweedFS credentials (from a Kubernetes Secret)
    S3_BUCKET                          input/output bucket (default: datasets)
    INPUT_KEY                          input object key (default: commands_pool.jsonl)
    OUTPUT_PREFIX                      output key prefix (default: teacher/)
    OLLAMA_HOST                        e.g. http://host.docker.internal:11434
    TEACHER_MODEL                      default: phi3.5
    TEACHER_TEMPERATURE                default: 0
    TEACHER_SEED                       default: 42
    PROMPT_PATH                        default: prompts/teacher_prompt.txt (baked into the image)
    CHECKPOINT_EVERY                   upload partial progress every N records (default: 25)
"""
import io
import json
import os
import sys
import time

import boto3
import requests
from botocore.client import Config


def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and (v is None or v == ""):
        sys.exit(f"missing required environment variable {name}")
    return v


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=env("S3_ENDPOINT", required=True),
        aws_access_key_id=env("S3_ACCESS_KEY", required=True),
        aws_secret_access_key=env("S3_SECRET_KEY", required=True),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def load_prompt_template():
    path = env("PROMPT_PATH", "prompts/teacher_prompt.txt")
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_input_lines(s3, bucket, key):
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read().decode("utf-8")
    return [ln for ln in body.splitlines() if ln.strip()]


def read_existing_output(s3, bucket, key):
    """Return {id: line} already present in this shard's output object, for resume."""
    done = {}
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return done
    except Exception:
        return done
    for ln in obj["Body"].read().decode("utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            done[json.loads(ln)["id"]] = ln
        except Exception:
            continue
    return done


def ask_teacher(host, model, template, command, temperature, seed):
    prompt = template.replace("{command}", command)
    resp = requests.post(
        f"{host}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "seed": seed},
        },
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def upload_lines(s3, bucket, key, lines):
    body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    s3.put_object(Bucket=bucket, Key=key, Body=io.BytesIO(body), ContentLength=len(body))


def main():
    shard_index = int(env("SHARD_INDEX", required=True))
    shard_count = int(env("SHARD_COUNT", required=True))
    if not (0 <= shard_index < shard_count):
        sys.exit(f"SHARD_INDEX {shard_index} must be in 0..{shard_count - 1}")

    bucket = env("S3_BUCKET", "datasets")
    input_key = env("INPUT_KEY", "commands_pool.jsonl")
    output_key = env("OUTPUT_PREFIX", "teacher/") + f"shard-{shard_index}.jsonl"
    ollama_host = env("OLLAMA_HOST", "http://host.docker.internal:11434").rstrip("/")
    model = env("TEACHER_MODEL", "phi3.5")
    temperature = float(env("TEACHER_TEMPERATURE", "0"))
    seed = int(env("TEACHER_SEED", "42"))
    checkpoint_every = int(env("CHECKPOINT_EVERY", "25"))

    s3 = s3_client()
    template = load_prompt_template()
    all_lines = read_input_lines(s3, bucket, input_key)

    # Select this shard's records by line number modulo.
    mine = []
    for line_no, raw in enumerate(all_lines):
        if line_no % shard_count != shard_index:
            continue
        rec = json.loads(raw)
        mine.append((rec["id"], rec["text"]))

    done = read_existing_output(s3, bucket, output_key)
    out_lines = [done[cid] for cid, _ in mine if cid in done]  # keep already-done, in order
    print(f"[shard {shard_index}/{shard_count}] {len(mine)} commands, "
          f"{len(done)} already done, {len(mine) - len(done)} to do", flush=True)

    processed_since_checkpoint = 0
    for cid, text in mine:
        if cid in done:
            continue
        t0 = time.time()
        try:
            raw_reply = ask_teacher(ollama_host, model, template, text, temperature, seed)
        except Exception as e:  # keep going; a failed call is just stored as an empty reply marker
            raw_reply = ""
            print(f"[shard {shard_index}] {cid} ERROR: {e}", flush=True)
        out_lines.append(json.dumps({"id": cid, "text": text, "teacher_raw": raw_reply},
                                    ensure_ascii=False))
        processed_since_checkpoint += 1
        print(f"[shard {shard_index}] {cid} ({time.time() - t0:.1f}s)", flush=True)
        if processed_since_checkpoint >= checkpoint_every:
            upload_lines(s3, bucket, output_key, out_lines)
            processed_since_checkpoint = 0
            print(f"[shard {shard_index}] checkpoint: {len(out_lines)} records uploaded", flush=True)

    upload_lines(s3, bucket, output_key, out_lines)
    print(f"[shard {shard_index}] DONE: wrote s3://{bucket}/{output_key} "
          f"({len(out_lines)} records)", flush=True)


if __name__ == "__main__":
    main()
