# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Make the cloud detector's two files from OmniCloudMask (``docs/specs/clouds.md`` 4.1).

OmniCloudMask (DPIRD, MIT licence) ships PyTorch networks; the app runs them with OpenCV, which
reads ONNX and runs both only once they are simplified (their shapes worked out in advance).
This script does that once, outside the app, in an environment of its own::

    uv venv /tmp/ocm --python 3.12
    uv pip install --python /tmp/ocm/bin/python \\
        omnicloudmask==1.8.1 onnx onnxsim opencv-python-headless
    /tmp/ocm/bin/python tools/clouds/export_detector.py <out folder>

and prints the size and SHA-256 of each file, which ``orthostudio/clouds/model.py`` names. It
then checks that OpenCV gives the classes PyTorch gives on a random patch.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

PATCH = 1000


def main(out: Path) -> None:
    import cv2
    import numpy as np
    import onnx
    import torch
    from omnicloudmask.download_models import get_models
    from omnicloudmask.model_utils import load_model_from_weights
    from onnxsim import simplify

    out.mkdir(parents=True, exist_ok=True)
    x = torch.from_numpy(
        np.random.default_rng(0).standard_normal((1, 3, PATCH, PATCH)).astype(np.float32)
    )
    for m in get_models(model_version=None):
        name = m["timm_model_name"].replace("tu-", "")
        model = load_model_from_weights(
            m["timm_model_name"], m["Path"], m["model_library"], torch.device("cpu")
        )
        raw = out / f"{name}.raw.onnx"
        torch.onnx.export(
            model,
            x,
            raw,
            input_names=["image"],
            output_names=["logits"],
            opset_version=17,
            dynamo=False,
        )
        simple, ok = simplify(onnx.load(raw))
        if not ok:
            raise SystemExit(f"{name}: onnxsim could not check the simplified network")
        path = out / f"{name}.onnx"
        onnx.save(simple, path)
        raw.unlink()
        with torch.no_grad():
            want = model(x).numpy().argmax(1)
        net = cv2.dnn.readNetFromONNX(str(path))
        net.setInput(x.numpy())
        same = float((net.forward().argmax(1) == want).mean())
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        print(f"{path.name}: {len(data)} bytes, sha256 {digest}")
        print(f"  same class as PyTorch on {100 * same:.3f} % of a random patch")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "clouds-detector-1"))
