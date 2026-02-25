# app/triton_infer.py

import time
from typing import Dict, Tuple, Optional

import numpy as np
from tritonclient.http import InferenceServerClient, InferInput, InferRequestedOutput


def triton_client(url: str) -> InferenceServerClient:
    # remove scheme if present
    url = url.replace("http://", "").replace("https://", "")
    return InferenceServerClient(url=url, verbose=False)


def infer(
    triton_url: str,
    model_name: str,
    inputs_np: Dict[str, np.ndarray],
    model_version: Optional[str] = None,
) -> Tuple[Dict[str, np.ndarray], float]:

    client = triton_client(triton_url)

    # 🔥 Triton requires model_version to be a string
    model_version = model_version or ""

    # 🔎 Get model metadata dynamically
    metadata = client.get_model_metadata(
        model_name=model_name,
        model_version=model_version
    )

    model_inputs = metadata["inputs"]
    model_outputs = metadata["outputs"]

    infer_inputs = []

    # 🔹 Build inputs dynamically
    for inp_meta in model_inputs:
        name = inp_meta["name"]
        dtype = inp_meta["datatype"]

        if name not in inputs_np:
            raise ValueError(
                f"Missing required input '{name}' for model '{model_name}'"
            )

        tensor = inputs_np[name]

        tr_input = InferInput(name, tensor.shape, dtype)
        tr_input.set_data_from_numpy(tensor, binary_data=True)

        infer_inputs.append(tr_input)

    # 🔹 Build outputs dynamically
    infer_outputs = [
        InferRequestedOutput(out_meta["name"], binary_data=True)
        for out_meta in model_outputs
    ]

    # 🔹 Execute inference
    t0 = time.perf_counter()

    response = client.infer(
        model_name=model_name,
        model_version=model_version,  # now guaranteed string
        inputs=infer_inputs,
        outputs=infer_outputs,
    )

    latency_ms = (time.perf_counter() - t0) * 1000.0

    # 🔹 Collect outputs dynamically
    outputs_np = {
        out_meta["name"]: response.as_numpy(out_meta["name"])
        for out_meta in model_outputs
    }

    return outputs_np, latency_ms