# app/tasks/classification.py

import numpy as np
from .base import BaseTask
from app.codecs import decode_base64_image_to_nchw_fp32


class ClassificationTask(BaseTask):
    name = "classification"

    def encode_inputs(self, payload, preprocess_config):
        img = decode_base64_image_to_nchw_fp32(payload["image_b64"])
        return {"INPUT__0": img}

    def decode_outputs(self, outputs, postprocess_config):
        logits = outputs["OUTPUT__0"]
        probs = logits.squeeze().tolist()
        return {
            "top1": int(np.argmax(probs)),
            "scores": probs,
        }
