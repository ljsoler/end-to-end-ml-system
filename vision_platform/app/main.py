import os
import uuid
import random
import asyncio

from fastapi import FastAPI, HTTPException, Request  # type: ignore
from fastapi.responses import Response  # type: ignore
from prometheus_client import (
    Gauge,
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)  # type: ignore

from .schemas import InferenceRequest, InferenceResponse
from .db import init_db, insert_result
from .triton_infer import infer as triton_infer
from .model_registry import get_model_entry
from .shadow_db import insert_shadow_result
from app.tasks.registry import TASK_REGISTRY
from enum import Enum


# ---------------------------------------------------------
# Prometheus: request/latency (main path)
# ---------------------------------------------------------
REQUEST_COUNT = Counter(
    "gateway_requests_total",
    "Total inference requests",
    ["model_name", "model_version", "version_type", "task_type", "machine_id", "camera_id", "status"],
)

INFERENCE_LATENCY = Histogram(
    "gateway_inference_latency_ms",
    "Inference latency in milliseconds",
    ["model_name", "task_type", "machine_id", "camera_id"],
    buckets=(1, 5, 10, 20, 50, 100, 200, 500, 1000),
)


# ---------------------------------------------------------
# Prometheus: shadow metrics
# Keep Gauge for "latest", and Histogram for distribution
# ---------------------------------------------------------
SHADOW_AGREEMENT_LATEST = Gauge(
    "gateway_shadow_agreement_latest",
    "Latest agreement score between primary and shadow outputs (0..1)",
    ["model_name", "task_type", "primary_version", "shadow_version", "machine_id", "camera_id"],
)

SHADOW_AGREEMENT = Histogram(
    "gateway_shadow_agreement",
    "Agreement score distribution between primary and shadow outputs (0..1)",
    ["model_name", "task_type", "primary_version", "shadow_version", "machine_id", "camera_id"],
    buckets=(0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 0.97, 0.99, 1.0),
)

SHADOW_LATENCY = Histogram(
    "gateway_shadow_latency_ms",
    "Shadow inference latency in milliseconds",
    ["model_name", "task_type", "shadow_version", "machine_id", "camera_id"],
    buckets=(1, 5, 10, 20, 50, 100, 200, 500, 1000),
)


class ModelVersion(str, Enum):
    stable = "stable"
    canary = "canary"


app = FastAPI(title="Vision Gateway (Task-Agnostic)")


# ---------------------------------------------------------
# Environment variables
# ---------------------------------------------------------
TRITON_URL = os.getenv("TRITON_URL", "triton.serving.svc.cluster.local:8000")
PG_DSN = os.getenv(
    "PG_DSN",
    "postgresql://postgres:postgres@postgres-postgresql.data.svc.cluster.local:5432/visiondb",
)


@app.on_event("startup")
async def startup():
    pool = await init_db(PG_DSN)
    app.state.db_pool = pool
    print("DB POOL:", pool, flush=True)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/infer", response_model=InferenceResponse)
async def infer_endpoint(request: Request, req: InferenceRequest):
    trace_id = uuid.uuid4().hex
    machine_id = (req.metadata or {}).get("machine_id", "unknown")  # type: ignore
    camera_id = (req.metadata or {}).get("camera_id", "unknown")  # type: ignore

    selected_version: str | None = None
    version_label: str | None = None
    task_type: str | None = None

    try:
        entry = await get_model_entry(request.app.state.db_pool, req.model_name)

        print("[debug] shadow cfg:", entry.get("shadow_version"), entry.get("shadow_percent"), flush=True)

        if not entry or not entry.get("active", False):
            REQUEST_COUNT.labels(
                model_name=req.model_name,
                model_version=selected_version or "unknown",
                version_type=version_label or "unknown",
                task_type=task_type or "unknown",
                machine_id=machine_id,
                camera_id=camera_id,
                status="error",
            ).inc()
            raise HTTPException(status_code=404, detail="Model not active")

        stable_version = str(entry["stable_version"])
        canary_version = entry.get("canary_version")
        canary_percent = int(entry.get("canary_percent") or 0)

        # -------------------------
        # Choose stable vs canary
        # -------------------------
        if canary_version and random.random() < (canary_percent / 100.0):
            selected_version = str(canary_version)
            version_label = ModelVersion.canary.value
        else:
            selected_version = stable_version
            version_label = ModelVersion.stable.value

        task_type = str(entry["task_type"])

        task = TASK_REGISTRY.get(task_type)
        if not task:
            raise HTTPException(status_code=500, detail=f"No Task registered for task_type '{task_type}'")

        # Ensure task has compare() if shadow is enabled (we'll check again later)
        # Not strictly required for main inference.
        # -------------------------
        # Encode inputs once (reuse for shadow)
        # -------------------------
        inputs_np = task.encode_inputs(req.payload, entry["preprocess_config"])

        # -------------------------
        # Main inference (stable/canary)
        # -------------------------
        outputs_np, latency_ms = triton_infer(
            TRITON_URL,
            req.model_name,
            inputs_np,
            model_version=selected_version,
        )

        predictions = task.decode_outputs(outputs_np, entry["postprocess_config"])

        # Persist main result
        await insert_result(
            request.app.state.db_pool,
            trace_id=trace_id,
            task_type=task_type,
            model_name=req.model_name,
            latency_ms=latency_ms,
            metadata=req.metadata,  # type: ignore
            prediction=predictions,
            raw_image_key=None,
        )

        # Metrics main path
        REQUEST_COUNT.labels(
            model_name=req.model_name,
            model_version=selected_version,
            version_type=version_label,
            task_type=task_type,
            machine_id=machine_id,
            camera_id=camera_id,
            status="success",
        ).inc()

        INFERENCE_LATENCY.labels(
            model_name=req.model_name,
            task_type=task_type,
            machine_id=machine_id,
            camera_id=camera_id,
        ).observe(latency_ms)

        # -------------------------
        # Shadow inference (async, non-blocking)
        # -------------------------
        primary_version_used = selected_version  # can be stable OR canary
        shadow_version = entry.get("shadow_version")
        shadow_percent = int(entry.get("shadow_percent") or 0)

        # If shadow points to the same version, skip
        if shadow_version and str(shadow_version) == str(primary_version_used):
            shadow_version = None

        # Only shadow if enabled and task supports compare()
        shadow_enabled = bool(shadow_version) and shadow_percent > 0
        if shadow_enabled and not hasattr(task, "compare"):
            # safer to disable shadow silently than to break inference
            print(f"[shadow] Task '{task_type}' missing compare(); shadow disabled for {req.model_name}", flush=True)
            shadow_enabled = False

        async def run_shadow(shadow_v: str, primary_v: str):
            try:
                shadow_outputs_np, shadow_latency_ms = triton_infer(
                    TRITON_URL,
                    req.model_name,
                    inputs_np,
                    model_version=shadow_v,
                )

                shadow_pred = task.decode_outputs(shadow_outputs_np, entry["postprocess_config"])

                # compare() should return dict including "agreement" float in [0,1]
                comparison = task.compare(predictions, shadow_pred, cfg={})  # type: ignore
                agreement = float(comparison.get("agreement", 0.0))

                await insert_shadow_result(
                    request.app.state.db_pool,
                    trace_id=trace_id,
                    model_name=req.model_name,
                    task_type=task_type,
                    stable_version=str(primary_v),  # column name kept as stable_version for DB compatibility
                    shadow_version=str(shadow_v),
                    machine_id=machine_id,
                    camera_id=camera_id,
                    stable_latency_ms=float(latency_ms),
                    shadow_latency_ms=float(shadow_latency_ms),
                    comparison=comparison,
                )

                SHADOW_LATENCY.labels(
                    model_name=req.model_name,
                    task_type=task_type,
                    shadow_version=str(shadow_v),
                    machine_id=machine_id,
                    camera_id=camera_id,
                ).observe(float(shadow_latency_ms))

                SHADOW_AGREEMENT_LATEST.labels(
                    model_name=req.model_name,
                    task_type=task_type,
                    primary_version=str(primary_v),
                    shadow_version=str(shadow_v),
                    machine_id=machine_id,
                    camera_id=camera_id,
                ).set(agreement)

                SHADOW_AGREEMENT.labels(
                    model_name=req.model_name,
                    task_type=task_type,
                    primary_version=str(primary_v),
                    shadow_version=str(shadow_v),
                    machine_id=machine_id,
                    camera_id=camera_id,
                ).observe(agreement)

            except Exception as e:
                # Never break main request due to shadow
                print(f"[shadow] error for {req.model_name}: {e}", flush=True)

        # ---- trigger shadow ----
        if shadow_enabled:
            if random.random() < (shadow_percent / 100.0):
                asyncio.create_task(run_shadow(str(shadow_version), str(primary_version_used)))

        # -------------------------
        # Response
        # -------------------------
        return InferenceResponse(
            model_name=req.model_name,
            model_version=selected_version,
            latency_ms=latency_ms,
            predictions=predictions,
            trace_id=trace_id,
        )

    except Exception:
        REQUEST_COUNT.labels(
            model_name=req.model_name,
            model_version=selected_version or "unknown",
            version_type=version_label or "unknown",
            task_type=task_type or "unknown",
            machine_id=machine_id,
            camera_id=camera_id,
            status="error",
        ).inc()
        raise