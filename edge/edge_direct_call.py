import requests
import numpy as np
from PIL import Image

img = Image.open("edge/sample_images/test.jpg").convert("RGB")
img = img.resize((224, 224))

arr = np.array(img).astype(np.float32) / 255.0
arr = np.transpose(arr, (2, 0, 1))  # HWC -> CHW
arr = np.expand_dims(arr, axis=0)

payload = {
    "inputs": [
        {
            "name": "INPUT__0",
            "shape": list(arr.shape),
            "datatype": "FP32",
            "data": arr.flatten().tolist()
        }
    ]
}

response = requests.post(
    "http://localhost:8000/v2/models/identity_onnx/infer",
    json=payload,
)

print(response.status_code)
print(response.json()["outputs"][0]["shape"])