import os
import numpy as np
from tritonclient.http import InferenceServerClient, InferInput, InferRequestedOutput

TRITON_URL = os.environ.get("TRITON_URL", "127.0.0.1:8000")
MODEL = os.environ["MODEL"]
VERSION = os.environ["VERSION"]

client = InferenceServerClient(url=TRITON_URL, verbose=False)

# batch=1 tensor
x = np.zeros((1,3,224,224), dtype=np.float32)

# small signal
x[:, :, :10, :10] = 1.0

inp = InferInput("INPUT__0", x.shape, "FP32")
inp.set_data_from_numpy(x, binary_data=True)

out = InferRequestedOutput("OUTPUT__0", binary_data=True)

resp = client.infer(
    model_name=MODEL,
    model_version=str(VERSION),
    inputs=[inp],
    outputs=[out],
)

y = resp.as_numpy("OUTPUT__0")

assert y is not None
assert y.shape[1:] == (3,224,224)

print("Smoke OK")
print("Output shape:", y.shape)
print("Mean:", float(y.mean()))