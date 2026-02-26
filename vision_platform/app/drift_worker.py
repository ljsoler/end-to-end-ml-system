import os
import io
import json
from datetime import datetime, timedelta, timezone

import asyncpg # type: ignore
import asyncio
import pandas as pd
import requests
import boto3 # type: ignore

from evidently.report import Report # type: ignore
from evidently.metric_preset import DataDriftPreset # type: ignore

# -----------------------------
# Env
# -----------------------------
PG_DSN = os.getenv("PG_DSN")
MODEL_NAME = os.getenv("MODEL_NAME", "identity_onnx")
TASK_TYPE = os.getenv("TASK_TYPE", "identity")

PROM_PUSHGATEWAY = os.getenv(
    "PROM_PUSHGATEWAY",
    "http://pushgateway-prometheus-pushgateway.monitoring.svc.cluster.local:9091",
)

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio.data.svc.cluster.local:9000")
MINIO_ACCESS = os.getenv("MINIO_ACCESS", "minio")
MINIO_SECRET = os.getenv("MINIO_SECRET", "minio123456")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "raw-images")  # you can create a separate bucket, e.g. "reports"

REPORT_BUCKET = os.getenv("REPORT_BUCKET", "reports")
REPORT_PREFIX = os.getenv("REPORT_PREFIX", "drift-reports")

# Windows
CURRENT_WINDOW_MIN = int(os.getenv("CURRENT_WINDOW_MIN", "60"))      # last 60 minutes
REFERENCE_WINDOW_HOURS = int(os.getenv("REFERENCE_WINDOW_HOURS", "24"))  # 24h reference
REFERENCE_LOOKBACK_DAYS = int(os.getenv("REFERENCE_LOOKBACK_DAYS", "7")) # from 7 days ago

# Features to monitor (simple & robust)
# We will derive these from stored prediction JSONB.
FEATURES = ["pred_mean", "pred_max"]  # for identity_onnx dummy; extend per task

# -----------------------------
# Helpers
# -----------------------------
def utcnow():
    return datetime.now(timezone.utc)

def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS,
        aws_secret_access_key=MINIO_SECRET,
        region_name="us-east-1",
    )

def ensure_bucket(s3, bucket: str):
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)

def prom_push(metric_lines: str, job: str, grouping: dict):
    # Pushgateway grouping key: /metrics/job/<job>/<k>/<v>/...
    url = f"{PROM_PUSHGATEWAY}/metrics/job/{job}"
    for k, v in grouping.items():
        url += f"/{k}/{v}"
    r = requests.put(url, data=metric_lines.encode("utf-8"), timeout=10)
    r.raise_for_status()

async def fetch_window(pool, model_name: str, camera_id: str, t0: datetime, t1: datetime) -> pd.DataFrame:
    # Pull minimal fields; parse prediction JSONB into numeric features
    sql = """
      SELECT created_at, metadata, prediction
      FROM inference_results
      WHERE model_name=$1
        AND (metadata->>'camera_id') = $2
        AND created_at >= $3 AND created_at < $4
      ORDER BY created_at ASC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, model_name, camera_id, t0, t1)

    records = []
    for r in rows:
        pred = r["prediction"] or {}

        if isinstance(pred, str):
            pred = json.loads(pred)
        # For your current identity demo output:
        # predictions: {"mean":..., "max":..., "shape":[...]}
        records.append({
            "ts": r["created_at"],
            "pred_mean": float(pred.get("mean", 0.0)),
            "pred_max": float(pred.get("max", 0.0)),
        })
    return pd.DataFrame.from_records(records)

def build_evidently_report(df_ref: pd.DataFrame, df_cur: pd.DataFrame) -> tuple[float, float, str]:
    """
    Returns:
      drift_score: share_drifted_features (0..1) as a stable single number
      share_drifted_features: same (kept twice for clarity)
      html_report: HTML string
    """
    # Evidently expects pure feature columns (no timestamps)
    ref = df_ref[FEATURES].copy()
    cur = df_cur[FEATURES].copy()

    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=ref, current_data=cur)

    # Extract metrics from JSON (stable)
    j = report.as_dict()
    # DataDriftPreset usually includes dataset_drift/share_drifted_features
    # We'll find it robustly:
    drift_share = 0.0
    dataset_drift = 0.0

    for m in j.get("metrics", []):
        if m.get("metric") == "DatasetDriftMetric":
            res = m.get("result", {})
            dataset_drift = float(res.get("dataset_drift", 0.0))
            drift_share = float(res.get("share_drifted_features", 0.0))
            break

    html = report.get_html()
    return drift_share, drift_share, html  # drift_score = share drifted features

async def insert_drift(pool, *, model_name, task_type, camera_id,
                       window_start, window_end, reference_start, reference_end,
                       drift_score, share_drifted_features, n_ref, n_cur, report_key):
    sql = """
      INSERT INTO model_drift_metrics(
        model_name, task_type, camera_id,
        window_start, window_end, reference_start, reference_end,
        drift_score, share_drifted_features, n_ref, n_cur, report_key
      )
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
    """
    async with pool.acquire() as conn:
        await conn.execute(
            sql,
            model_name, task_type, camera_id,
            window_start, window_end, reference_start, reference_end,
            drift_score, share_drifted_features, n_ref, n_cur, report_key
        )

async def main():
    if not PG_DSN:
        raise RuntimeError("PG_DSN missing")

    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=5)

    # Discover cameras from recent metadata
    # (simple approach; later you can maintain a camera registry)
    async with pool.acquire() as conn:
        cams = await conn.fetch(
            """
            SELECT DISTINCT (metadata->>'camera_id') AS camera_id
            FROM inference_results
            WHERE model_name=$1
              AND metadata ? 'camera_id'
              AND created_at > now() - interval '24 hours'
            """,
            MODEL_NAME
        )
    camera_ids = [c["camera_id"] for c in cams if c["camera_id"]]

    if not camera_ids:
        print("No camera_ids found in inference_results. Skipping.", flush=True)
        return

    now = utcnow()
    cur_end = now
    cur_start = now - timedelta(minutes=CURRENT_WINDOW_MIN)

    ref_end = (now - timedelta(days=REFERENCE_LOOKBACK_DAYS))
    ref_start = ref_end - timedelta(hours=REFERENCE_WINDOW_HOURS)

    s3 = s3_client()
    ensure_bucket(s3, REPORT_BUCKET)

    for camera_id in camera_ids:
        df_cur = await fetch_window(pool, MODEL_NAME, camera_id, cur_start, cur_end)
        df_ref = await fetch_window(pool, MODEL_NAME, camera_id, ref_start, ref_end)

        n_cur = len(df_cur)
        n_ref = len(df_ref)

        # Minimum sample sizes (tune later)
        if n_cur < 30 or n_ref < 200:
            print(f"[{MODEL_NAME}/{camera_id}] Not enough data ref={n_ref} cur={n_cur}, skipping.", flush=True)
            continue

        drift_score, share_drifted, html = build_evidently_report(df_ref, df_cur)

        ts = now.strftime("%Y%m%dT%H%M%SZ")
        key = f"{REPORT_PREFIX}/{MODEL_NAME}/{camera_id}/{ts}.html"

        s3.put_object(
            Bucket=REPORT_BUCKET,
            Key=key,
            Body=html.encode("utf-8"),
            ContentType="text/html",
        )

        await insert_drift(
            pool,
            model_name=MODEL_NAME,
            task_type=TASK_TYPE,
            camera_id=camera_id,
            window_start=cur_start,
            window_end=cur_end,
            reference_start=ref_start,
            reference_end=ref_end,
            drift_score=drift_score,
            share_drifted_features=share_drifted,
            n_ref=n_ref,
            n_cur=n_cur,
            report_key=key,
        )

        # Push Prometheus metric
        # One time series per model+camera
        metric = (
            f'gateway_drift_score{{model_name="{MODEL_NAME}",camera_id="{camera_id}",task_type="{TASK_TYPE}"}} {drift_score}\n'
            f'gateway_drift_share_drifted_features{{model_name="{MODEL_NAME}",camera_id="{camera_id}",task_type="{TASK_TYPE}"}} {share_drifted}\n'
            f'gateway_drift_n_ref{{model_name="{MODEL_NAME}",camera_id="{camera_id}",task_type="{TASK_TYPE}"}} {n_ref}\n'
            f'gateway_drift_n_cur{{model_name="{MODEL_NAME}",camera_id="{camera_id}",task_type="{TASK_TYPE}"}} {n_cur}\n'
        )
        prom_push(metric, job="evidently-drift", grouping={"model": MODEL_NAME, "camera": camera_id})

        print(f"[{MODEL_NAME}/{camera_id}] drift={drift_score:.3f} report=s3://{REPORT_BUCKET}/{key}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())