from pydantic import BaseModel, Field
from typing import Any, Dict, Literal, Optional

TaskType = Literal["identity_test", "detection", "segmentation", "classification", "anomaly"]

class InferenceRequest(BaseModel):
    task_type: TaskType
    image_b64: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class InferenceResponse(BaseModel):
    task_type: TaskType
    model_name: str
    model_version: Optional[str] = None
    latency_ms: float
    predictions: Any
    trace_id: str