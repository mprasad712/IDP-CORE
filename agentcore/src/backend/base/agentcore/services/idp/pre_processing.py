import cv2
import numpy as np
import fitz
from pathlib import Path
from loguru import logger

def deskew_image(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Detect and correct rotation skew (minor angles) in a document image.

    Returns the corrected image and the skew angle in degrees.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    # Invert image to get white text on black background for contour bounding box detection
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]

    # Compute rotated bounding box for all non-zero pixels
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) == 0:
        return image, 0.0

    angle = cv2.minAreaRect(coords)[-1]

    # Adjust the angle value returned by minAreaRect
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # If the angle is close to 90 or -90, it is axis-aligned (flat), so set to 0.0
    if abs(angle) > 85.0:
        angle = 0.0

    # Ignore extremely small rotations to avoid unnecessary interpolation
    if abs(angle) < 0.1:
        return image, 0.0

    # Rotate the image to correct skew
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    return rotated, angle

def _is_vertical_layout(image: np.ndarray) -> bool:
    """Coarse orientation: True if text blocks are predominantly vertical (page rotated 90/270),
    False if horizontal (0/180). Uses connected-component aspect ratios. This CANNOT tell 0 from
    180 or 90 from 270 — that needs reading direction (see ``detect_rotation_angle``)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
    dilated = cv2.dilate(thresh, kernel, iterations=2)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    horiz_count = vert_count = 0
    for c in contours:
        _, _, w, h = cv2.boundingRect(c)
        if w > 30 and h > 10:
            if w > h * 1.5:
                horiz_count += 1
            elif h > w * 1.5:
                vert_count += 1
    return vert_count > horiz_count


def _ocr_text_score(image: np.ndarray) -> float:
    """Sum of OCR token confidences for ``image`` (higher = reads better in this orientation).

    Uses the shared PaddleOCR instance with the angle classifier DISABLED (cls=False) so the raw
    page orientation actually matters. Returns -1.0 if OCR is unavailable (e.g. no paddle locally)."""
    try:
        from agentcore.services.idp.ocr import get_ocr_instance
        model = get_ocr_instance("en")
        if model is None:
            return -1.0  # signal "OCR unavailable" to the caller
        res = model.ocr(image, cls=False)
        lines = res[0] if res and res[0] else []
        return sum(float(ln[1][1]) for ln in lines if ln and len(ln) > 1 and len(ln[1]) > 1)
    except Exception as e:  # pragma: no cover - OCR is best-effort here
        logger.debug(f"[Rotation] OCR orientation scoring failed: {e}")
        return -1.0


def detect_rotation_angle(image: np.ndarray) -> int:
    """Detect the rotation (0/90/180/270) that must be APPLIED to make the page upright.

    When OCR is available it scores all four orientations and returns whichever reads best — this
    reliably distinguishes 0 from 180 (upside-down) and 90 from 270, independent of any heuristic.
    When OCR is unavailable (e.g. PaddleOCR not installed locally) it falls back to a coarse
    connected-component heuristic that can only tell 0 from 90 — it CANNOT detect 180/270 (a
    documented limitation; correct on OCR-equipped machines).
    """
    best_angle, best_score = 0, -1.0
    ocr_available = False
    for angle in (0, 90, 180, 270):
        rotated = rotate_image(image, angle) if angle else image
        score = _ocr_text_score(rotated)
        if score >= 0.0:
            ocr_available = True
            if score > best_score:
                best_score, best_angle = score, angle

    if ocr_available:
        return best_angle
    # OCR unavailable -> coarse heuristic (0 vs 90 only; the 180-flip is not resolvable here).
    return 90 if _is_vertical_layout(image) else 0

def rotate_image(image: np.ndarray, angle: int) -> np.ndarray:
    """Rotate image by 90, 180, or 270 degrees."""
    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    elif angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    elif angle == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image

async def detect_and_correct_skew(file_bytes: bytes, file_type: str) -> tuple[bytes, float]:
    """Detect and correct document skew for both images and multi-page PDFs.

    Returns the corrected file bytes and the detected skew angle.
    """
    file_type = file_type.lower().strip(".")
    if file_type == "pdf":
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            doc_out = fitz.open()
            avg_angle = 0.0
            page_count = len(doc)

            for page_num in range(page_count):
                page = doc[page_num]
                pix = page.get_pixmap(dpi=150)
                page_img_bytes = pix.tobytes("png")

                # Process page image
                nparr = np.frombuffer(page_img_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                corrected_img, angle = deskew_image(img)
                avg_angle += angle

                # Encode corrected page to bytes
                _, encoded_page = cv2.imencode(".png", corrected_img)
                corrected_page_bytes = encoded_page.tobytes()

                # Recompile page to output PDF
                img_doc = fitz.open(stream=corrected_page_bytes, filetype="png")
                pdf_bytes = img_doc.convert_to_pdf()
                img_doc.close()

                page_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                doc_out.insert_pdf(page_doc)
                page_doc.close()

            out_bytes = doc_out.tobytes()
            doc.close()
            doc_out.close()
            return out_bytes, (avg_angle / page_count if page_count > 0 else 0.0)

        except Exception as e:
            logger.error(f"[Pre-Processing] PDF deskew failed: {e}")
            return file_bytes, 0.0
    else:
        # Process image
        try:
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return file_bytes, 0.0
            corrected_img, angle = deskew_image(img)

            _, encoded_img = cv2.imencode(f".{file_type}", corrected_img)
            return encoded_img.tobytes(), angle
        except Exception as e:
            logger.error(f"[Pre-Processing] Image deskew failed: {e}")
            return file_bytes, 0.0

async def detect_and_correct_rotation(file_bytes: bytes, file_type: str) -> tuple[bytes, int]:
    """Detect and correct document page rotation (90/180/270 degrees).

    Returns the corrected file bytes and the detected rotation angle.
    """
    file_type = file_type.lower().strip(".")
    if file_type == "pdf":
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            doc_out = fitz.open()
            detected_angle = 0

            for page_num in range(len(doc)):
                page = doc[page_num]
                pix = page.get_pixmap(dpi=150)
                page_img_bytes = pix.tobytes("png")

                # Process page image
                nparr = np.frombuffer(page_img_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                angle = detect_rotation_angle(img)
                if page_num == 0:
                    detected_angle = angle  # Use first page rotation as document rotation

                corrected_img = rotate_image(img, angle)

                # Encode corrected page to bytes
                _, encoded_page = cv2.imencode(".png", corrected_img)
                corrected_page_bytes = encoded_page.tobytes()

                # Recompile page to output PDF
                img_doc = fitz.open(stream=corrected_page_bytes, filetype="png")
                pdf_bytes = img_doc.convert_to_pdf()
                img_doc.close()

                page_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                doc_out.insert_pdf(page_doc)
                page_doc.close()

            out_bytes = doc_out.tobytes()
            doc.close()
            doc_out.close()
            return out_bytes, detected_angle

        except Exception as e:
            logger.error(f"[Pre-Processing] PDF rotation correction failed: {e}")
            return file_bytes, 0
    else:
        # Process image
        try:
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return file_bytes, 0
            angle = detect_rotation_angle(img)
            corrected_img = rotate_image(img, angle)

            _, encoded_img = cv2.imencode(f".{file_type}", corrected_img)
            return encoded_img.tobytes(), angle
        except Exception as e:
            logger.error(f"[Pre-Processing] Image rotation correction failed: {e}")
            return file_bytes, 0
