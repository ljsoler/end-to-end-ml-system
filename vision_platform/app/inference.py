import os
import random
import requests

def _normalize_triton_base(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return f"http://{url}"
    return url

TRITON_BASE = _normalize_triton_base(
    os.getenv("TRITON_URL", "triton.serving.svc.cluster.local:8000")
).rstrip("/")

MODEL_NAME = "identity_onnx"
CANARY_PERCENT = float(os.getenv("CANARY_PERCENT", "10"))

def choose_version():
    if random.random() < CANARY_PERCENT / 100:
        return "2"
    return "1"

def infer(tensor):
    version = choose_version()

    url = f"{TRITON_BASE}/v2/models/{MODEL_NAME}/versions/{version}/infer"

    payload = {
        "inputs": [{
            "name": "INPUT__0",
            "shape": list(tensor.shape),
            "datatype": "FP32",
            "data": tensor.flatten().tolist()
        }]
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
    except requests.RequestException as exc:
        raise RuntimeError(f"Triton request failed: {exc}") from exc

    if r.status_code != 200:
        raise RuntimeError(
            f"Triton error {r.status_code}: {r.text}"
        )

    return r.json(), version
