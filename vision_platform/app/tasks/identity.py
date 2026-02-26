# app/tasks/identity.py

from .base import BaseTask
from app.codecs import decode_base64_image_to_nchw_fp32


class IdentityTask(BaseTask):
    name = "identity"

    def encode_inputs(self, payload, preprocess_config):
        resize = preprocess_config.get("resize", [224, 224])
        img = decode_base64_image_to_nchw_fp32(
            payload["image_b64"],
            size=resize
        )
        return {"INPUT__0": img}

    def decode_outputs(self, outputs, postprocess_config):
        y = outputs["OUTPUT__0"]
        return {
            "mean": float(y.mean()),
            "max": float(y.max()),
            "shape": list(y.shape),
        }