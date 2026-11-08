"""Clean the teacher shard outputs into datasets/v1/train.jsonl (Task 1, step 6).

Reads every shard object (datasets/teacher/shard-*.jsonl), rejects bad records,
and writes the survivors in id order to datasets/v1/train.jsonl together with its
SHA-256 in datasets/v1/train.sha256.

Each input record is placed in EXACTLY ONE bucket (checks run in order), so:
    input = (a) + (b) + (c) + (d) + duplicates + kept

Rejection rules:
  (a) teacher reply is not valid JSON (no parseable JSON object)
  (b) keys are not exactly {intent, date, time, place}
  (c) intent is not one of the eight
  (d) a slot value is neither null nor text that appears in the command
  (e) duplicate command (keep the smallest id)   -> counted as "duplicates"

Output line format (canonical, deterministic ordering for a reproducible checksum):
    {"id": "...", "text": "...", "intent": "...", "date": ..., "time": ..., "place": ...}

Usage:
  # from object storage (default; needs S3_* env vars):
  python worker/clean.py
  # from a local directory of shard files, writing locally (offline / CI):
  python worker/clean.py --local-shards path/to/shards --local-out datasets_local
"""
import argparse
import glob
import hashlib
import io
import json
import os

KEYS = ("intent", "date", "time", "place")
INTENTS = {
    "alarm_set", "alarm_query", "calendar_set", "calendar_query",
    "calendar_remove", "weather_query", "datetime_query", "recommendation_events",
}


def norm(v):
    return None if v is None else " ".join(str(v).strip().lower().split())


def extract_json_object(raw):
    """Return the parsed JSON object from a teacher reply, or None.

    Tries a strict parse first, then falls back to the first balanced {...} block
    (teacher models sometimes add a code fence or a stray word). Deterministic.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # fall back: first balanced brace block
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(s[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    return None


def slot_ok(value, text):
    """A slot is valid if it is null, or its normalised text appears in the command."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    nv = norm(value)
    if nv == "":
        return True
    return nv in norm(text)


def load_shards_s3():
    import boto3
    from botocore.client import Config
    bucket = os.environ.get("S3_BUCKET", "datasets")
    prefix = os.environ.get("TEACHER_PREFIX", "teacher/")
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    records = []
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".jsonl"):
                keys.append(obj["Key"])
    for key in sorted(keys):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        for ln in body.splitlines():
            if ln.strip():
                records.append(json.loads(ln))
    return records, s3, bucket


def load_shards_local(path):
    records = []
    for fp in sorted(glob.glob(os.path.join(path, "shard-*.jsonl"))):
        with open(fp, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    records.append(json.loads(ln))
    return records


def clean(records):
    counts = {"input": len(records), "a_invalid_json": 0, "b_wrong_keys": 0,
              "c_bad_intent": 0, "d_bad_slot": 0, "duplicates": 0, "kept": 0}
    survivors = []  # (id, cleaned_record)
    for rec in records:
        cid, text = rec["id"], rec["text"]
        obj = extract_json_object(rec.get("teacher_raw"))
        if obj is None:
            counts["a_invalid_json"] += 1
            continue
        if set(obj.keys()) != set(KEYS):
            counts["b_wrong_keys"] += 1
            continue
        if obj["intent"] not in INTENTS:
            counts["c_bad_intent"] += 1
            continue
        if not all(slot_ok(obj[k], text) for k in ("date", "time", "place")):
            counts["d_bad_slot"] += 1
            continue
        cleaned = {"id": cid, "text": text, "intent": obj["intent"],
                   "date": obj["date"], "time": obj["time"], "place": obj["place"]}
        survivors.append((cid, text, cleaned))

    # (e) duplicate commands: keep the smallest id per normalised command text.
    best = {}  # norm_text -> (id, cleaned)
    for cid, text, cleaned in survivors:
        key = norm(text)
        if key not in best or cid < best[key][0]:
            best[key] = (cid, cleaned)
    kept = sorted(best.values(), key=lambda t: t[0])
    counts["duplicates"] = len(survivors) - len(kept)
    counts["kept"] = len(kept)
    lines = [json.dumps(c, ensure_ascii=False) for _, c in kept]
    return counts, lines


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--local-shards", help="read shard-*.jsonl from this local dir instead of S3")
    ap.add_argument("--local-out", help="write train.jsonl + checksum under this local dir instead of S3")
    a = ap.parse_args()

    s3 = bucket = None
    if a.local_shards:
        records = load_shards_local(a.local_shards)
    else:
        records, s3, bucket = load_shards_s3()

    counts, lines = clean(records)
    body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    digest = hashlib.sha256(body).hexdigest()
    checksum_line = f"{digest}  train.jsonl\n"

    if a.local_out:
        out_dir = os.path.join(a.local_out, "v1")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "train.jsonl"), "wb") as f:
            f.write(body)
        with open(os.path.join(out_dir, "train.sha256"), "w", encoding="utf-8") as f:
            f.write(checksum_line)
        print(f"wrote {out_dir}/train.jsonl and train.sha256")
    else:
        s3.put_object(Bucket=bucket, Key="v1/train.jsonl",
                      Body=io.BytesIO(body), ContentLength=len(body))
        cs = checksum_line.encode("utf-8")
        s3.put_object(Bucket=bucket, Key="v1/train.sha256",
                      Body=io.BytesIO(cs), ContentLength=len(cs))
        print(f"wrote s3://{bucket}/v1/train.jsonl and v1/train.sha256")

    assert counts["input"] == (counts["a_invalid_json"] + counts["b_wrong_keys"]
                               + counts["c_bad_intent"] + counts["d_bad_slot"]
                               + counts["duplicates"] + counts["kept"]), "counts do not reconcile"

    print("\n=== Task 1 cleaning counts ===")
    print(f"  input records              : {counts['input']}")
    print(f"  (a) invalid JSON           : {counts['a_invalid_json']}")
    print(f"  (b) wrong keys             : {counts['b_wrong_keys']}")
    print(f"  (c) bad intent             : {counts['c_bad_intent']}")
    print(f"  (d) bad slot value         : {counts['d_bad_slot']}")
    print(f"  duplicates                 : {counts['duplicates']}")
    print(f"  kept                       : {counts['kept']}")
    print(f"  SHA-256(train.jsonl)       : {digest}")
    print("  reconciles: input == a+b+c+d+duplicates+kept  ✓")


if __name__ == "__main__":
    main()
