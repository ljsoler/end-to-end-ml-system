# app/tasks/identity.py
from __future__ import annotations
from .base import BaseTask
from app.codecs import decode_base64_image_to_nchw_fp32
import numpy as np

class IdentityTask(BaseTask):
    name = "identity"

    def encode_inputs(self, payload, preprocess_config):
        resize = preprocess_config.get("resize", [224, 224])
        img = decode_base64_image_to_nchw_fp32(payload["image_b64"], size=resize)
        # IMPORTANT: key must match Triton input name(s) for identity model
        return {"INPUT__0": img}

    def decode_outputs(self, outputs, postprocess_config):
        # outputs name must match Triton output(s)
        y = outputs["OUTPUT__0"]
        return {"mean": float(y.mean()), "max": float(y.max()), "shape": list(y.shape)}

    def compare(self, stable_pred: dict, shadow_pred: dict, cfg: dict) -> dict:
        # Since this identity example returns summary stats, compare those.
        # In real identity you would compare embeddings similarity/cosine.
        mean_diff = abs(float(stable_pred.get("mean", 0.0)) - float(shadow_pred.get("mean", 0.0)))
        max_diff = abs(float(stable_pred.get("max", 0.0)) - float(shadow_pred.get("max", 0.0)))

        # Convert to an agreement score in [0,1] (simple proxy)
        # agreement=1 means identical
        agreement = float(np.exp(-(mean_diff + max_diff)))

        return {
            "mean_diff": mean_diff,
            "max_diff": max_diff,
            "agreement": agreement
        }