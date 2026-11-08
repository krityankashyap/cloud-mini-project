"""S3 helpers for SeaweedFS (bucket creation, uploads, downloads, checksums).

Reads S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY from the environment.
Typically run against a kubectl port-forward of svc/seaweedfs-s3 (localhost:8333).

Subcommands:
  create-buckets datasets models
  upload-file <local> <bucket> <key>
  upload-dir  <localdir> <bucket> <prefix>      # also writes <prefix>/model.sha256
  download-dir <bucket> <prefix> <localdir>
  sha256-dir  <localdir>                        # print the canonical directory checksum
"""
import argparse
import hashlib
import os

import boto3
from botocore.client import Config


def client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT", "http://localhost:8333"),
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def dir_sha256(localdir):
    """Deterministic checksum over all files (sorted by relative path)."""
    h = hashlib.sha256()
    files = []
    for root, _, names in os.walk(localdir):
        for n in names:
            full = os.path.join(root, n)
            files.append((os.path.relpath(full, localdir), full))
    for rel, full in sorted(files):
        h.update(rel.encode("utf-8"))
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-buckets"); p.add_argument("buckets", nargs="+")
    p = sub.add_parser("upload-file"); p.add_argument("local"); p.add_argument("bucket"); p.add_argument("key")
    p = sub.add_parser("upload-dir"); p.add_argument("localdir"); p.add_argument("bucket"); p.add_argument("prefix"); p.add_argument("--no-sha", action="store_true")
    p = sub.add_parser("download-dir"); p.add_argument("bucket"); p.add_argument("prefix"); p.add_argument("localdir")
    p = sub.add_parser("sha256-dir"); p.add_argument("localdir")
    p = sub.add_parser("write-sha256"); p.add_argument("localfile"); p.add_argument("bucket"); p.add_argument("key")
    a = ap.parse_args()

    if a.cmd == "sha256-dir":
        print(dir_sha256(a.localdir))
        return

    s3 = client()
    if a.cmd == "create-buckets":
        for b in a.buckets:
            try:
                s3.create_bucket(Bucket=b)
                print(f"created bucket {b}")
            except Exception as e:
                print(f"bucket {b}: {e} (continuing)")
    elif a.cmd == "upload-file":
        s3.upload_file(a.local, a.bucket, a.key)
        print(f"uploaded {a.local} -> s3://{a.bucket}/{a.key}")
    elif a.cmd == "upload-dir":
        prefix = a.prefix.rstrip("/")
        for root, _, names in os.walk(a.localdir):
            for n in names:
                full = os.path.join(root, n)
                rel = os.path.relpath(full, a.localdir)
                key = f"{prefix}/{rel}"
                s3.upload_file(full, a.bucket, key)
                print(f"uploaded {rel} -> s3://{a.bucket}/{key}")
        if not a.no_sha:
            digest = dir_sha256(a.localdir)
            body = f"{digest}  {prefix}\n".encode("utf-8")
            s3.put_object(Bucket=a.bucket, Key=f"{prefix}/model.sha256", Body=body)
            print(f"wrote s3://{a.bucket}/{prefix}/model.sha256 = {digest}")
    elif a.cmd == "write-sha256":
        h = hashlib.sha256()
        with open(a.localfile, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        body = f"{h.hexdigest()}  {os.path.basename(a.localfile)}\n".encode("utf-8")
        s3.put_object(Bucket=a.bucket, Key=a.key, Body=body)
        print(f"wrote s3://{a.bucket}/{a.key} = {h.hexdigest()}")
    elif a.cmd == "download-dir":
        prefix = a.prefix.rstrip("/") + "/"
        os.makedirs(a.localdir, exist_ok=True)
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=a.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                rel = obj["Key"][len(prefix):]
                if not rel:
                    continue
                dest = os.path.join(a.localdir, rel)
                os.makedirs(os.path.dirname(dest) or a.localdir, exist_ok=True)
                s3.download_file(a.bucket, obj["Key"], dest)
                print(f"downloaded s3://{a.bucket}/{obj['Key']} -> {dest}")


if __name__ == "__main__":
    main()
