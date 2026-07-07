#!/usr/bin/env python3
"""Download the PP-LCNet_x1_0_doc_ori orientation model used by Fix Rotation (Scan Corrector).

Run once after cloning (or wire into setup):
    python scripts/download_orientation_model.py

Fetches the official PaddleX inference model (~6.9 MB) into:
    base/agentcore/services/idp/models/PP-LCNet_x1_0_doc_ori/

At runtime this is loaded by services/idp/doc_orientation.py's native-Paddle tier, which needs
`paddlepaddle` installed. For a lighter, paddle-free runtime, convert this model to ONNX (see
scripts/export_orientation_model.py); the ONNX path takes tier priority when present.

Stdlib only — works on Windows / Linux / macOS with no extra deps.
"""
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

_URL = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/"
    "official_inference_model/paddle3.0.0/PP-LCNet_x1_0_doc_ori_infer.tar"
)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_MODELS_DIR = os.path.abspath(
    os.path.join(_SCRIPT_DIR, "..", "base", "agentcore", "services", "idp",
                 "models", "PP-LCNet_x1_0_doc_ori")
)
_REQUIRED = ("inference.json", "inference.pdiparams")
_COPY = ("inference.json", "inference.pdiparams", "inference.yml", "config.json")


def main() -> int:
    if all(os.path.exists(os.path.join(_MODELS_DIR, f)) for f in _REQUIRED):
        print(f"OK  orientation model already present: {_MODELS_DIR}")
        return 0

    os.makedirs(_MODELS_DIR, exist_ok=True)
    print(f"Downloading orientation model (~6.9 MB) …\n  {_URL}")
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = os.path.join(tmp, "model.tar")
        urllib.request.urlretrieve(_URL, tar_path)
        with tarfile.open(tar_path) as t:
            t.extractall(tmp)
        src = next(
            (os.path.join(tmp, n) for n in os.listdir(tmp)
             if os.path.isdir(os.path.join(tmp, n))
             and os.path.exists(os.path.join(tmp, n, "inference.json"))),
            None,
        )
        if src is None:
            print("ERROR: inference.json not found in the downloaded archive")
            return 1
        for f in _COPY:
            s = os.path.join(src, f)
            if os.path.exists(s):
                shutil.copy2(s, os.path.join(_MODELS_DIR, f))

    ok = all(os.path.exists(os.path.join(_MODELS_DIR, f)) for f in _REQUIRED)
    print(f"{'OK  saved to' if ok else 'ERROR incomplete at'}: {_MODELS_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
