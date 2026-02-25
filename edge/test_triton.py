import requests
import numpy as np
import time

# Batch of 4
tensor = np.random.rand(4, 3, 224, 224).astype(np.float32)

payload = {
    "inputs": [
        {
            "name": "INPUT__0",
            "shape": list(tensor.shape),
            "datatype": "FP32",
            "data": tensor.flatten().tolist()
        }
    ]
}

start = time.time()
response = requests.post(
    "http://localhost:8000/v2/models/identity_onnx/infer",
    json=payload,
)
end = time.time()

print("Status:", response.status_code)
print("Latency:", end - start)
print("Output shape:", response.json()["outputs"][0]["shape"])