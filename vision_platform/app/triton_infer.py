import time
import numpy as np
from tritonclient.http import InferenceServerClient, InferInput, InferRequestedOutput

def triton_client(url: str) -> InferenceServerClient:
    return InferenceServerClient(url=url, verbose=False)

def preprocess_minimal(_: bytes) -> np.ndarray:
    # Minimal deterministic input (replace with real image decode later)
    x = np.zeros((1, 3, 224, 224), dtype=np.float32)
    x[:, :, :10, :10] = 1.0
    return x

def infer(triton_url: str, model_name: str, img_bytes: bytes):
    c = triton_client(triton_url)
    x = preprocess_minimal(img_bytes)

    inp = InferInput("INPUT__0", x.shape, "FP32")
    inp.set_data_from_numpy(x, binary_data=True)

    out = InferRequestedOutput("OUTPUT__0", binary_data=True)

    t0 = time.perf_counter()
    resp = c.infer(model_name=model_name, inputs=[inp], outputs=[out])
    latency_ms = (time.perf_counter() - t0) * 1000.0

    y = resp.as_numpy("OUTPUT__0")
    pred = {"mean": float(y.mean()), "max": float(y.max()), "shape": list(y.shape)}
    return pred, latency_ms