"""Document orientation classifier — 0 / 90 / 180 / 270 degrees.

Priority chain (first available wins):

1. ONNX via onnxruntime   — production path; zero PaddlePaddle dependency at
                            inference time.  The ONNX file is generated once:
                              python scripts/export_orientation_model.py
                            and baked into the Docker image at build time.

2. PaddleX pipeline       — dev fallback when the ONNX model has not been
                            exported yet (e.g. fresh local checkout on Windows).
                            Uses PP-LCNet_x1_0_doc_ori via the PaddleX
                            doc_orientation_classify pipeline.

3. OpenCV heuristic       — last resort when neither model is available.
                            Reliably detects 90°/270° (sideways documents) via
                            projection-profile variance.  Cannot distinguish
                            0° from 180°; defaults to 0° for that case.
"""

import os

import cv2
import numpy as np
from loguru import logger

# ── paths ─────────────────────────────────────────────────────────────────────

_ONNX_PATH = os.path.join(os.path.dirname(__file__), "models", "doc_orientation.onnx")
_ANGLES = [0, 90, 180, 270]

# ImageNet normalisation — PP-LCNet is pretrained on ImageNet, fine-tuned on docs
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)

# ── lazy globals ──────────────────────────────────────────────────────────────

_ort_session = None
_ort_input_hw: tuple[int, int] = (224, 224)
_ort_input_name: str = "x"
_ort_tried: bool = False

_paddlex_pipeline = None
_paddlex_tried: bool = False

# tier 1.5: native Paddle inference. Used when the ONNX model is absent but the Paddle inference model
# files (models/PP-LCNet_x1_0_doc_ori/) + paddlepaddle are present. Reliable (unlike the OpenCV
# heuristic) without the paddle2onnx export step, which fails on Windows.
_PADDLE_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models", "PP-LCNet_x1_0_doc_ori")
_paddle_predictor = None
_paddle_in_name: str = ""
_paddle_out_name: str = ""
_paddle_tried: bool = False


# ── tier 1: ONNX ─────────────────────────────────────────────────────────────

def _load_ort_session():
    global _ort_session, _ort_input_hw, _ort_input_name, _ort_tried
    if _ort_tried:
        return _ort_session
    _ort_tried = True

    if not os.path.exists(_ONNX_PATH):
        logger.info(
            "[Orientation] ONNX model not found ({}). "
            "Run scripts/export_orientation_model.py to generate it. "
            "Trying PaddleX model as fallback.",
            _ONNX_PATH,
        )
        return None

    try:
        import onnxruntime as ort  # noqa: PLC0415

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 2
        opts.log_severity_level = 3  # suppress INFO noise

        _ort_session = ort.InferenceSession(
            _ONNX_PATH,
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )

        meta = _ort_session.get_inputs()[0]
        _ort_input_name = meta.name
        shape = meta.shape  # [batch, channels, H, W]
        h = int(shape[2]) if isinstance(shape[2], int) and shape[2] > 0 else 224
        w = int(shape[3]) if isinstance(shape[3], int) and shape[3] > 0 else 224
        _ort_input_hw = (h, w)
        logger.info("[Orientation] ONNX model loaded — input {}x{}", h, w)
    except ImportError:
        logger.warning("[Orientation] onnxruntime not installed. Trying PaddleX fallback.")
    except Exception as exc:
        logger.warning("[Orientation] ONNX load failed: {}. Trying PaddleX fallback.", exc)

    return _ort_session


def _predict_onnx(image: np.ndarray) -> int | None:
    sess = _load_ort_session()
    if sess is None:
        return None
    try:
        h, w = _ort_input_hw
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA)
        x = (rgb.astype(np.float32) / 255.0 - _MEAN) / _STD
        x = x.transpose(2, 0, 1)[np.newaxis, :]  # 1×3×H×W
        logits = sess.run(None, {_ort_input_name: x})[0][0]  # (4,)
        label = int(np.argmax(logits))
        exp_l = np.exp(logits - np.max(logits))
        conf = float(exp_l[label] / exp_l.sum())
        angle = _ANGLES[label]
        logger.info("[Orientation] ONNX -> angle={} deg (conf={:.2f})", angle, conf)
        return angle
    except Exception as exc:
        logger.warning("[Orientation] ONNX inference failed: {}", exc)
        return None


# ── tier 1.5: native Paddle inference (local model files) ─────────────────────

def _load_paddle_predictor():
    global _paddle_predictor, _paddle_tried, _paddle_in_name, _paddle_out_name
    if _paddle_tried:
        return _paddle_predictor
    _paddle_tried = True
    json_f = os.path.join(_PADDLE_MODEL_DIR, "inference.json")
    params_f = os.path.join(_PADDLE_MODEL_DIR, "inference.pdiparams")
    if not (os.path.exists(json_f) and os.path.exists(params_f)):
        return None
    try:
        from paddle.inference import Config, create_predictor  # noqa: PLC0415

        cfg = Config(json_f, params_f)
        cfg.disable_gpu()
        # NOTE: do NOT enable_memory_optim()/switch_ir_optim() — they make create_predictor fail on
        # this PIR (.json) model ("Not find predictor_id 0 and pass_name memory_optimize_pass").
        try:
            cfg.disable_glog_info()
        except Exception:  # noqa: BLE001
            pass
        pred = create_predictor(cfg)
        _paddle_in_name = pred.get_input_names()[0]
        _paddle_out_name = pred.get_output_names()[0]
        _paddle_predictor = pred
        logger.info("[Orientation] Paddle native inference model loaded ({})", _PADDLE_MODEL_DIR)
    except Exception as exc:  # noqa: BLE001 — degrade to the next tier
        logger.warning("[Orientation] Paddle native model unavailable: {}", exc)
    return _paddle_predictor


def _predict_paddle_native(image: np.ndarray) -> int | None:
    """Reliable orientation via the local Paddle inference model (needs only paddlepaddle, no ONNX
    conversion, no paddleocr/cv2 conflict). Same 224×224 ImageNet preprocessing as the ONNX path."""
    pred = _load_paddle_predictor()
    if pred is None:
        return None
    try:
        h, w = _ort_input_hw
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA)
        x = ((rgb.astype(np.float32) / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)[np.newaxis, :].copy()
        in_handle = pred.get_input_handle(_paddle_in_name)
        in_handle.copy_from_cpu(x)
        pred.run()
        logits = pred.get_output_handle(_paddle_out_name).copy_to_cpu()[0]
        angle = _ANGLES[int(np.argmax(logits))]
        logger.info("[Orientation] Paddle native -> angle={} deg", angle)
        return angle
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Orientation] Paddle native inference failed: {}", exc)
        return None


# ── tier 2: PaddleX pipeline ──────────────────────────────────────────────────

def _load_paddlex_model():
    global _paddlex_pipeline, _paddlex_tried
    if _paddlex_tried:
        return _paddlex_pipeline
    _paddlex_tried = True
    try:
        from paddlex import create_model  # noqa: PLC0415

        _paddlex_pipeline = create_model("PP-LCNet_x1_0_doc_ori")
        logger.info("[Orientation] PaddleX PP-LCNet_x1_0_doc_ori model loaded")
    except Exception as exc:
        logger.warning("[Orientation] PaddleX model unavailable: {}", exc)
    return _paddlex_pipeline


def _predict_paddlex(image: np.ndarray) -> int | None:
    model = _load_paddlex_model()
    if model is None:
        return None
    try:
        results = list(model.predict(image))
        if not results:
            return None
        first = results[0]
        # Returns {'label_names': ['90'], 'scores': array([0.99]), 'class_ids': array([[1]])}
        label_names = first.get("label_names") or []
        if label_names:
            label = label_names[0]
        else:
            class_ids = first.get("class_ids", [])
            ids = class_ids.flat if hasattr(class_ids, "flat") else iter(class_ids)
            label = str(_ANGLES[int(next(ids, 0))])

        angle = int(label) if str(label).lstrip("-").isdigit() else 0
        scores = first.get("scores", [])
        conf = float(scores[0]) if len(scores) > 0 else 0.0
        logger.info("[Orientation] PaddleX -> angle={} deg (conf={:.2f})", angle, conf)
        return angle
    except Exception as exc:
        logger.warning("[Orientation] PaddleX inference failed: {}", exc)
        return None


# ── tier 3: OpenCV heuristic ──────────────────────────────────────────────────

def _predict_opencv(image: np.ndarray) -> int:
    """Projection-profile heuristic. Reliable for 90°/270°; defaults to 0° for 0°/180°."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)

    h_var = float(np.var(np.sum(binary, axis=1).astype(np.float64)))
    v_var = float(np.var(np.sum(binary, axis=0).astype(np.float64)))
    logger.info("[Orientation] OpenCV heuristic h_var={:.0f} v_var={:.0f}", h_var, v_var)

    # Strongly vertical text density → document is sideways
    if v_var > h_var * 1.5:
        return 90
    return 0  # 0 vs 180 requires the model; 0 is the safe default


# ── public API ────────────────────────────────────────────────────────────────

def predict_orientation(image: np.ndarray) -> int:
    """Return the rotation angle (0 / 90 / 180 / 270) to apply to make the document upright.

    0 means no correction is needed.
    Tries ONNX -> PaddleX -> OpenCV in that order.
    """
    if image is None or image.size == 0:
        return 0

    result = _predict_onnx(image)
    if result is not None:
        return result

    result = _predict_paddle_native(image)
    if result is not None:
        return result

    result = _predict_paddlex(image)
    if result is not None:
        return result

    return _predict_opencv(image)
