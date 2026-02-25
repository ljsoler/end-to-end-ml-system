import os
import uuid
from fastapi import FastAPI, HTTPException, UploadFile, Request
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

from .schemas import InferenceRequest, InferenceResponse
from .router import resolve_route
from .storage import decode_b64_image, s3_client, make_object_key
from .db import init_db, insert_result
from .triton_infer import infer as triton_infer
from .inference import infer as canary_infer
from .preprocessing import preprocess
from app.tasks.registry import TASK_REGISTRY
from .model_registry import get_model_entry

app = FastAPI(title="Vision Gateway (Task-Agnostic)")

# Prometheus metrics
REQUEST_COUNT = Counter(
    "gateway_requests_total",
    "Total inference requests",
    ["model_name", "task_type", "machine_id", "camera_id", "status"]
)

INFERENCE_LATENCY = Histogram(
    "gateway_inference_latency_ms",
    "Inference latency in milliseconds",
    ["model_name", "task_type", "machine_id", "camera_id"],
    buckets=(1, 5, 10, 20, 50, 100, 200, 500, 1000)
)

# Environment variables
TRITON_URL = os.getenv("TRITON_URL", "triton.serving.svc.cluster.local:8000")
PG_DSN = os.getenv("PG_DSN", "postgresql://postgres:postgres@postgres-postgresql.data.svc.cluster.local:5432/visiondb")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio.data.svc.cluster.local:9000")
MINIO_ACCESS = os.getenv("MINIO_ACCESS", "minio")
MINIO_SECRET = os.getenv("MINIO_SECRET", "minio123456")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "raw-images")


@app.on_event("startup")
async def startup():
    pool = await init_db(PG_DSN)
    print("DB POOL:", pool)
    app.state.db_pool = pool

@app.post("/predict")
async def predict(file: UploadFile):
    tensor = preprocess(file)
    result, version = canary_infer(tensor)

    return {
        "model_version": version
        # "prediction": result
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/infer", response_model=InferenceResponse)
async def infer_endpoint(request: Request, req: InferenceRequest):

    trace_id = uuid.uuid4().hex
    machine_id = req.metadata.get("machine_id", "unknown")
    camera_id = req.metadata.get("camera_id", "unknown")

    try:
        entry = await get_model_entry(
            request.app.state.db_pool,
            req.model_name
        )

        if not entry or not entry["active"]:
            REQUEST_COUNT.labels(
                model_name=req.model_name,
                task_type="unknown",
                machine_id=machine_id,
                camera_id=camera_id,
                status="error"
            ).inc()
            raise HTTPException(status_code=404, detail="Model not active")

        task_type = entry["task_type"]

        task = TASK_REGISTRY.get(task_type)

        inputs_np = task.encode_inputs(req.payload, entry["preprocess_config"])

        outputs_np, latency_ms = triton_infer(
            TRITON_URL,
            req.model_name,
            inputs_np
        )

        predictions = task.decode_outputs(
            outputs_np,
            entry["postprocess_config"]
        )

        await insert_result(
            request.app.state.db_pool,
            trace_id=trace_id,
            task_type=task_type,
            model_name=req.model_name,
            latency_ms=latency_ms,
            metadata=req.metadata,
            prediction=predictions,
            raw_image_key=None,
        )

        REQUEST_COUNT.labels(
            model_name=req.model_name,
            task_type=task_type,
            machine_id=machine_id,
            camera_id=camera_id,
            status="success"
        ).inc()

        INFERENCE_LATENCY.labels(
            model_name=req.model_name,
            task_type=task_type,
            machine_id=machine_id,
            camera_id=camera_id
        ).observe(latency_ms)

        return InferenceResponse(
            model_name=req.model_name,
            model_version=None,
            latency_ms=latency_ms,
            predictions=predictions,
            trace_id=trace_id,
        )

    except Exception:
        REQUEST_COUNT.labels(
            model_name=req.model_name,
            task_type="unknown",
            machine_id=machine_id,
            camera_id=camera_id,
            status="error"
        ).inc()
        raise