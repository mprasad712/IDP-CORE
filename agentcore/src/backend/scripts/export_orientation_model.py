#!/usr/bin/env python3
"""Export PP-LCNet_x1_0_doc_ori from PaddleX/PaddleOCR to ONNX format.

Run ONCE during Docker build or initial dev setup:
    python scripts/export_orientation_model.py

Requires:
    pip install paddle2onnx paddlepaddle paddleocr

The output ONNX file is written to:
    base/agentcore/services/idp/models/doc_orientation.onnx

The file is ~6-8 MB and should be committed to the repository (or baked into
the Docker image at build time) so the runtime has no dependency on PaddleX
for orientation detection — only onnxruntime is needed.
"""

import glob
import os
import sys


# ── output path ──────────────────────────────────────────────────────────────

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SCRIPT_DIR)
_MODELS_DIR = os.path.join(
    _BACKEND_DIR, "base", "agentcore", "services", "idp", "models"
)
_ONNX_OUT = os.path.join(_MODELS_DIR, "doc_orientation.onnx")


# ── helpers ──────────────────────────────────────────────────────────────────

def _find_model_dir() -> str | None:
    """Search common PaddleX / PaddlePaddle cache locations for the
    PP-LCNet_x1_0_doc_ori inference model files.

    PaddleX 3.x stores models under ~/.paddlex/official_models/{model_name}/ and
    uses inference.json (PIR graph) + inference.pdiparams (binary weights).
    """
    home = os.path.expanduser("~")
    candidates = [
        home,
        "/root",
        os.environ.get("PADDLEX_HOME", ""),
        os.environ.get("HOME", ""),
    ]

    # Direct known path — check this first (PaddleX 3.x default)
    for base in candidates:
        if not base:
            continue
        direct = os.path.join(base, ".paddlex", "official_models", "PP-LCNet_x1_0_doc_ori")
        if os.path.isdir(direct) and (
            os.path.exists(os.path.join(direct, "inference.json"))
            or os.path.exists(os.path.join(direct, "inference.pdmodel"))
        ):
            return direct

    # Broad recursive search as fallback
    search_bases = [
        os.path.join(c, sub)
        for c in candidates if c
        for sub in (".paddlex", ".paddlepaddle", ".paddle")
    ]
    for base in search_bases:
        if not os.path.isdir(base):
            continue
        for pattern in ("**/*doc_ori*", "**/*PP-LCNet*doc*"):
            for match in glob.glob(os.path.join(base, pattern), recursive=True):
                if os.path.isdir(match) and any(
                    f.endswith((".pdmodel", ".json")) and "inference" in f
                    for f in os.listdir(match)
                ):
                    return match

    return None


def _download_model() -> None:
    """Trigger PaddleOCR / PaddleX to download the orientation model."""
    print("  Initialising PaddleOCR with use_doc_orientation_classify=True …")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        from paddleocr import PaddleOCR  # noqa: PLC0415
        PaddleOCR(
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang="en",
            enable_mkldnn=False,
        )
        print("  Download complete.")
    except Exception as exc:
        # Non-fatal: the model may already be cached from a previous run.
        print(f"  Warning during init: {exc}")


def _export(model_dir: str) -> bool:
    """Convert the Paddle inference model in ``model_dir`` to ONNX.

    Supports both the old static-graph format (.pdmodel) and the new PIR
    format (.json) used by PaddlePaddle 3.x / PaddleX 3.x.
    """
    # PaddlePaddle 3.x PIR format uses inference.json + inference.pdiparams
    # PaddlePaddle 2.x static format uses inference.pdmodel + inference.pdiparams
    json_model = os.path.join(model_dir, "inference.json")
    pdmodel = os.path.join(model_dir, "inference.pdmodel")

    if os.path.exists(json_model):
        model_file = "inference.json"
        print("  Format: PaddlePaddle 3.x PIR (inference.json)")
    elif os.path.exists(pdmodel):
        model_file = "inference.pdmodel"
        print("  Format: PaddlePaddle 2.x static (inference.pdmodel)")
    else:
        pdmodel_files = glob.glob(os.path.join(model_dir, "*.pdmodel"))
        json_files = glob.glob(os.path.join(model_dir, "*.json"))
        if pdmodel_files:
            model_file = os.path.basename(pdmodel_files[0])
        elif json_files:
            model_file = os.path.basename(json_files[0])
        else:
            print(f"  ERROR: no .pdmodel or .json model file found in {model_dir}")
            return False

    pdiparams_files = glob.glob(os.path.join(model_dir, "*.pdiparams"))
    params_file = (
        os.path.basename(pdiparams_files[0])
        if pdiparams_files
        else model_file.rsplit(".", 1)[0] + ".pdiparams"
    )

    print(f"  Model:  {model_file}")
    print(f"  Params: {params_file}")

    try:
        import paddle2onnx  # noqa: PLC0415

        model_full = os.path.join(model_dir, model_file)
        params_full = os.path.join(model_dir, params_file)

        paddle2onnx.export(
            model_filename=model_full,
            params_filename=params_full,
            save_file=_ONNX_OUT,
            opset_version=11,
            enable_onnx_checker=True,
        )
        size_mb = os.path.getsize(_ONNX_OUT) / 1_048_576
        if size_mb < 0.1:
            print(f"  ERROR: output ONNX is only {size_mb:.2f} MB — PIR format not supported by this paddle2onnx version.")
            os.remove(_ONNX_OUT)
            return False
        print(f"  Exported ({size_mb:.1f} MB) -> {_ONNX_OUT}")
        return True
    except ImportError:
        print("  ERROR: paddle2onnx not installed — pip install paddle2onnx")
        return False
    except Exception as exc:
        print(f"  ERROR during export: {exc}")
        return False


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 60)
    print("PP-LCNet_x1_0_doc_ori -> ONNX export")
    print("=" * 60)

    os.makedirs(_MODELS_DIR, exist_ok=True)

    if os.path.exists(_ONNX_OUT):
        size_mb = os.path.getsize(_ONNX_OUT) / 1_048_576
        print(f"OK ONNX model already exists ({size_mb:.1f} MB): {_ONNX_OUT}")
        return 0

    print("\nStep 1: Download model via PaddleOCR …")
    _download_model()

    print("\nStep 2: Locate Paddle inference model files …")
    model_dir = _find_model_dir()
    if model_dir is None:
        print("  ERROR: PP-LCNet_x1_0_doc_ori not found in any PaddleX cache.")
        print("  Ensure paddleocr/paddlepaddle is installed and the model downloaded.")
        return 1
    print(f"  Found: {model_dir}")

    print("\nStep 3: Export to ONNX …")
    if not _export(model_dir):
        return 1

    print("\nDone. Commit (or bake into Docker) the file at:")
    print(f"   {_ONNX_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
