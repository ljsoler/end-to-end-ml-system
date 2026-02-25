# app/tasks/base.py

from abc import ABC, abstractmethod
import numpy as np
from typing import Dict, Any


class BaseTask(ABC):

    @abstractmethod
    def encode_inputs(self, payload, preprocess_config):
        pass

    @abstractmethod
    def decode_outputs(self, outputs, postprocess_config):
        pass