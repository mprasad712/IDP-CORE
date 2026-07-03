import asyncio

import cv2
import fitz
import numpy as np
from loguru import logger

from agentcore.services.idp.doc_orientation import predict_orientation


def deskew_image(image: np.ndarray, skew_threshold: float = 0.1) -> tuple[np.ndarray, float]:
    """Detect and correct minor skew (small rotation angles) in a document image.

    ``skew_threshold`` is the minimum absolute tilt (degrees) that triggers a correction; a
    smaller detected tilt is left untouched. The historical 0.1 floor is always enforced.

    Returns the corrected image and the skew angle in degrees.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]

    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) == 0:
        return image, 0.0

    angle = cv2.minAreaRect(coords)[-1]

    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) > 85.0:
        angle = 0.0

    if abs(angle) < max(skew_threshold, 0.1):
        return image, 0.0

    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return rotated, angle


def rotate_image(image: np.ndarray, angle: int) -> np.ndarray:
    """Rotate image by 0 / 90 / 180 / 270 degrees (lossless, integer multiples only).

    `angle` is the orientation the model detected — i.e. by how much the document has
    been rotated CW from upright.  The correction is therefore the INVERSE rotation:
      90  detected  (top points RIGHT) -> rotate 90° CCW to restore upright
      270 detected  (top points LEFT)  -> rotate 90° CW  to restore upright
    """
    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    return image


def detect_rotation_angle(image: np.ndarray) -> int:
    """Return the rotation angle (0 / 90 / 180 / 270) to apply to make the document upright.

    Delegates to the ONNX PP-LCNet_x1_0_doc_ori classifier via doc_orientation.py.
    Falls back to an OpenCV projection-profile heuristic when the ONNX model is absent.
    """
    return predict_orientation(image)


# ─────────────────────────────── sync implementations ────────────────────────

def _sync_correct_skew(file_bytes: bytes, file_type: str, skew_threshold: float = 0.1) -> tuple[bytes, float]:
    if file_type == "pdf":
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            doc_out = fitz.open()
            avg_angle = 0.0
            page_count = len(doc)
            for page_num in range(page_count):
                pix = doc[page_num].get_pixmap(dpi=150)
                nparr = np.frombuffer(pix.tobytes("png"), np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                corrected_img, angle = deskew_image(img, skew_threshold)
                avg_angle += angle
                _, encoded_page = cv2.imencode(".png", corrected_img)
                img_doc = fitz.open(stream=encoded_page.tobytes(), filetype="png")
                page_doc = fitz.open(stream=img_doc.convert_to_pdf(), filetype="pdf")
                doc_out.insert_pdf(page_doc)
                img_doc.close()
                page_doc.close()
            out_bytes = doc_out.tobytes()
            doc.close()
            doc_out.close()
            return out_bytes, (avg_angle / page_count if page_count > 0 else 0.0)
        except Exception as e:
            logger.error(f"[Pre-Processing] PDF deskew failed: {e}")
            return file_bytes, 0.0
    else:
        try:
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return file_bytes, 0.0
            corrected_img, angle = deskew_image(img, skew_threshold)
            _, encoded_img = cv2.imencode(f".{file_type}", corrected_img)
            return encoded_img.tobytes(), angle
        except Exception as e:
            logger.error(f"[Pre-Processing] Image deskew failed: {e}")
            return file_bytes, 0.0


def _sync_correct_rotation(
    file_bytes: bytes, file_type: str, allowed_angles: list[int] | None = None
) -> tuple[bytes, int]:
    # Only snap to the configured rotations; a detected angle outside the set is treated as
    # upright (no rotation). Default = the historical {90, 180, 270}.
    allowed = set(allowed_angles or [90, 180, 270])
    if file_type == "pdf":
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            doc_out = fitz.open()
            detected_angle = 0
            for page_num in range(len(doc)):
                pix = doc[page_num].get_pixmap(dpi=150)
                nparr = np.frombuffer(pix.tobytes("png"), np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                angle = detect_rotation_angle(img)
                if angle not in allowed:
                    angle = 0
                if page_num == 0:
                    detected_angle = angle
                corrected_img = rotate_image(img, angle)
                _, encoded_page = cv2.imencode(".png", corrected_img)
                img_doc = fitz.open(stream=encoded_page.tobytes(), filetype="png")
                page_doc = fitz.open(stream=img_doc.convert_to_pdf(), filetype="pdf")
                doc_out.insert_pdf(page_doc)
                img_doc.close()
                page_doc.close()
            out_bytes = doc_out.tobytes()
            doc.close()
            doc_out.close()
            return out_bytes, detected_angle
        except Exception as e:
            logger.error(f"[Pre-Processing] PDF rotation correction failed: {e}")
            return file_bytes, 0
    else:
        try:
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return file_bytes, 0
            angle = detect_rotation_angle(img)
            if angle not in allowed:
                angle = 0
            corrected_img = rotate_image(img, angle)
            _, encoded_img = cv2.imencode(f".{file_type}", corrected_img)
            return encoded_img.tobytes(), angle
        except Exception as e:
            logger.error(f"[Pre-Processing] Image rotation correction failed: {e}")
            return file_bytes, 0


# ─────────────────────────────── async public API ────────────────────────────

async def detect_and_correct_skew(
    file_bytes: bytes, file_type: str, skew_threshold: float = 0.1
) -> tuple[bytes, float]:
    """Detect and correct document skew. Runs in executor to avoid blocking the event loop."""
    file_type = file_type.lower().strip(".")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_correct_skew, file_bytes, file_type, skew_threshold)


async def detect_and_correct_rotation(
    file_bytes: bytes, file_type: str, allowed_angles: list[int] | None = None
) -> tuple[bytes, int]:
    """Detect and correct page rotation (90 / 180 / 270). Runs in executor to avoid blocking the event loop."""
    file_type = file_type.lower().strip(".")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_correct_rotation, file_bytes, file_type, allowed_angles)
