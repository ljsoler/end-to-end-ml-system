import os
import uuid
from fastapi import FastAPI, HTTPException, UploadFile
from .schemas import InferenceRequest, InferenceResponse
from .router import resolve_route
from .storage import decode_b64_image, s3_client, make_object_key
from .db import init_db, insert_result
from .triton_infer import infer as triton_infer
from .inference import infer as canary_infer
from .preprocessing import preprocess

app = FastAPI(title="Vision Gateway (Task-Agnostic)")

TRITON_URL = os.getenv("TRITON_URL", "triton.serving.svc.cluster.local:8000")
PG_DSN = os.getenv("PG_DSN", "postgresql://postgres:postgres@postgres-postgresql.data.svc.cluster.local:5432/visiondb")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio.data.svc.cluster.local:9000")
MINIO_ACCESS = os.getenv("MINIO_ACCESS", "minio")
MINIO_SECRET = os.getenv("MINIO_SECRET", "minio123456")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "raw-images")

@app.on_event("startup")
async def startup():
    await init_db(PG_DSN)

@app.post("/predict")
async def predict(file: UploadFile):
    tensor = preprocess(file)
    result, version = canary_infer(tensor)

    return {
        "model_version": version,
        "prediction": result
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/infer", response_model=InferenceResponse)
async def infer_endpoint(req: InferenceRequest):
    trace_id = uuid.uuid4().hex

    try:
        route = resolve_route(req.task_type)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    img_bytes = decode_b64_image(req.image_b64)

    # Save raw image
    s3 = s3_client(MINIO_ENDPOINT, MINIO_ACCESS, MINIO_SECRET)
    key = make_object_key("raw")
    try:
        s3.put_object(Bucket=MINIO_BUCKET, Key=key, Body=img_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MinIO error: {e}")

    # Triton inference
    prediction, latency_ms = triton_infer(TRITON_URL, route.triton_model, img_bytes)

    # Persist metadata + prediction
    await insert_result(
        PG_DSN,
        trace_id=trace_id,
        task_type=req.task_type,
        model_name=route.model_name,
        latency_ms=latency_ms,
        metadata=req.metadata,
        prediction=prediction,
        raw_image_key=key,
    )

    return InferenceResponse(
        task_type=req.task_type,
        model_name=route.model_name,
        model_version=None,
        latency_ms=latency_ms,
        predictions=prediction,
        trace_id=trace_id,
    )