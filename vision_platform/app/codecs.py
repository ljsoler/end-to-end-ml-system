from __future__ import annotations
import base64
from io import BytesIO
from typing import Any, Tuple

import numpy as np
from PIL import Image

TRITON_DTYPE_TO_NP = {
    "FP32": np.float32,
    "FP16": np.float16,
    "INT64": np.int64,
    "INT32": np.int32,
    "UINT8": np.uint8,
    "BOOL": np.bool_,
}

def decode_base64_image_to_nchw_fp32(b64: str, size: Tuple[int, int] | None = None) -> np.ndarray:
    """Decode base64 image -> float32 NCHW in [0,1]. No model-specific normalization."""
    raw = base64.b64decode(b64)
    img = Image.open(BytesIO(raw)).convert("RGB")
    if size is not None:
        img = img.resize(size)
    arr = np.asarray(img).astype(np.float32) / 255.0  # HWC
    arr = np.transpose(arr, (2, 0, 1))               # CHW
    arr = np.expand_dims(arr, axis=0)                # NCHW
    return arr

def decode_raw_base64(b64: str) -> bytes:
    return base64.b64decode(b64)

def ensure_numpy(value: Any, dtype: str | None = None) -> np.ndarray:
    """Convert generic JSON/list/scalar -> numpy."""
    if isinstance(value, np.ndarray):
        arr = value
    else:
        arr = np.asarray(value)

    if dtype:
        np_dtype = TRITON_DTYPE_TO_NP.get(dtype)
        if np_dtype is None:
            raise ValueError(f"Unsupported Triton dtype: {dtype}")
        arr = arr.astype(np_dtype, copy=False)

    return arr