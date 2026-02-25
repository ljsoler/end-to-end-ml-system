import base64
import json
import time
from pathlib import Path

import requests
import boto3
import psycopg2


GATEWAY_URL = "http://localhost:8082/infer"

# MinIO (port-forwarded)
MINIO_ENDPOINT = "http://localhost:9000"
MINIO_ACCESS = "minio"
MINIO_SECRET = "minio123456"
MINIO_BUCKET = "raw-images"

# Postgres (port-forwarded)
PG_HOST = "localhost"
PG_PORT = 5432
PG_DB = "visiondb"
PG_USER = "postgres"
PG_PASS = "postgres"

# Image to send
IMG_PATH = Path("edge/sample_images/test.jpg")


def ensure_bucket():
    s3 = boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS,
        aws_secret_access_key=MINIO_SECRET,
        region_name="us-east-1",
    )
    # Create if missing
    buckets = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if MINIO_BUCKET not in buckets:
        s3.create_bucket(Bucket=MINIO_BUCKET)
    return s3


def pg_connect():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
    )


def main():
    assert IMG_PATH.exists(), f"Missing test image at: {IMG_PATH}"

    # 1) Snapshot DB row count before
    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM inference_results;")
    before = cur.fetchone()[0]
    conn.close()

    # 2) Call gateway
    img_b64 = base64.b64encode(IMG_PATH.read_bytes()).decode("utf-8")
    payload = {
        "task_type": "identity_test",
        "image_b64": img_b64,
        "metadata": {"machine_id": "M01", "camera_id": "C01", "ts": time.time()},
    }

    t0 = time.time()
    r = requests.post(GATEWAY_URL, json=payload, timeout=60)
    dt = (time.time() - t0) * 1000.0

    print("HTTP:", r.status_code, f"({dt:.1f} ms)")
    r.raise_for_status()
    data = r.json()
    print("Response JSON:")
    print(json.dumps(data, indent=2))

    trace_id = data.get("trace_id")
    assert trace_id, "No trace_id returned by gateway"

    # 3) Verify DB row was inserted
    time.sleep(1.0)  # tiny delay to be safe
    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM inference_results;")
    after = cur.fetchone()[0]
    assert after == before + 1, f"DB row count did not increase (before={before}, after={after})"

    # Fetch the last row and validate fields
    cur.execute("""
        SELECT trace_id, task_type, model_name, latency_ms, raw_image_key
        FROM inference_results
        ORDER BY id DESC
        LIMIT 1;
    """)
    last = cur.fetchone()
    conn.close()

    print("\nLast DB row:")
    print(last)

    db_trace_id, task_type, model_name, latency_ms, raw_key = last
    assert db_trace_id == trace_id, "trace_id in DB does not match response"
    assert task_type == "identity_test", "task_type mismatch"
    assert model_name, "model_name empty"
    assert latency_ms is not None, "latency_ms missing"
    assert raw_key, "raw_image_key missing"

    # 4) Verify MinIO object exists
    s3 = ensure_bucket()
    obj = s3.head_object(Bucket=MINIO_BUCKET, Key=raw_key)
    size = obj["ContentLength"]
    print(f"\nMinIO object OK: s3://{MINIO_BUCKET}/{raw_key} (bytes={size})")
    assert size > 0, "Uploaded raw image has 0 bytes"

    print("\n✅ E2E Gateway test PASSED")


if __name__ == "__main__":
    main()