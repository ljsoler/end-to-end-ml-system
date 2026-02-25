from .classification import ClassificationTask
from .detection import DetectionTask
from .identity import IdentityTask

TASK_REGISTRY = {
    ClassificationTask.name: ClassificationTask(),
    DetectionTask.name: DetectionTask(),
    IdentityTask.name: IdentityTask(),
}