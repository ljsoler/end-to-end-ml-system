from dataclasses import dataclass

@dataclass(frozen=True)
class Route:
    model_name: str
    triton_model: str

ROUTES = {
    "identity_test": Route(model_name="identity_onnx", triton_model="identity_onnx"),
    "detection": Route(model_name="identity_onnx", triton_model="identity_onnx"),
    "segmentation": Route(model_name="identity_onnx", triton_model="identity_onnx"),
    "classification": Route(model_name="identity_onnx", triton_model="identity_onnx"),
    "anomaly": Route(model_name="identity_onnx", triton_model="identity_onnx"),
}

def resolve_route(task_type: str) -> Route:
    if task_type not in ROUTES:
        raise ValueError(f"Unknown task_type: {task_type}")
    return ROUTES[task_type]