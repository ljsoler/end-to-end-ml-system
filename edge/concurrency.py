import requests
import numpy as np
import concurrent.futures
import time

def send():
    tensor = np.random.rand(1,3,224,224).astype(np.float32)
    payload = {
        "inputs": [{
            "name": "INPUT__0",
            "shape": list(tensor.shape),
            "datatype": "FP32",
            "data": tensor.flatten().tolist()
        }]
    }
    r = requests.post(
        "http://localhost:8000/v2/models/identity_onnx/infer",
        json=payload,
    )
    return r.status_code

start = time.time()

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    results = list(executor.map(lambda _: send(), range(50)))

end = time.time()

print("Total time:", end - start)
print("Requests OK:", results.count(200))