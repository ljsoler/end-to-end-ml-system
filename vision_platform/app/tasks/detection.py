# app/tasks/detection.py

from .base import BaseTask
from app.codecs import decode_base64_image_to_nchw_fp32


class DetectionTask(BaseTask):
    name = "detection"

    def encode_inputs(self, payload, preprocess_config):
        resize = preprocess_config.get("resize", [224,224])
        img = decode_base64_image_to_nchw_fp32(payload["image_b64"], size=resize)
        return {"images": img}

    def decode_outputs(self, outputs, postprocess_config):
        boxes = outputs["boxes"].tolist()
        scores = outputs["scores"].tolist()
        labels = outputs["labels"].tolist()

        detections = []
        for b, s, l in zip(boxes, scores, labels):
            detections.append({
                "bbox": b,
                "score": s,
                "label": l
            })

        return {"detections": detections}