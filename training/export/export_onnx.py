import torch
import torch.nn as nn

class Identity(nn.Module):
    def forward(self, x):
        return x

if __name__ == "__main__":
    m = Identity().eval()
    dummy = torch.randn(1, 3, 224, 224)

    torch.onnx.export(
        m,
        dummy,
        "model.onnx",
        input_names=["INPUT__0"],
        output_names=["OUTPUT__0"],
        dynamic_axes={
            "INPUT__0": {0: "batch"},
            "OUTPUT__0": {0: "batch"},
        },
        opset_version=13,
    )

    print("Exported model.onnx with dynamic batch")