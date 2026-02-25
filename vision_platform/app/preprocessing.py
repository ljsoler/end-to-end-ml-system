import numpy as np
from PIL import Image
import io


def preprocess(upload_file):
    """
    Preprocess image for Triton ONNX model.

    Expected model input:
        shape: [batch, 3, 224, 224]
        dtype: float32
        range: [0,1]
    """

    # Read bytes
    contents = upload_file.file.read()

    # Load image
    img = Image.open(io.BytesIO(contents)).convert("RGB")

    # Resize
    img = img.resize((224, 224))

    # Convert to numpy
    arr = np.array(img).astype(np.float32) / 255.0

    # HWC → CHW
    arr = np.transpose(arr, (2, 0, 1))

    # Add batch dimension
    arr = np.expand_dims(arr, axis=0)

    return arr