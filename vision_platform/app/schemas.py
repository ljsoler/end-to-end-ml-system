from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel


class InferenceRequest(BaseModel):
    model_name: str
    payload: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = {}


class InferenceResponse(BaseModel):
    trace_id: str
    model_name: str
    model_version: Optional[str] = None
    latency_ms: float
    predictions: Dict[str, Any]