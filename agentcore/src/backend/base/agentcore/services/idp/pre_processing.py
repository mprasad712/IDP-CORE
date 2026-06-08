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

def detect_rotation_angle(image: np.ndarray) -> int:
    """Detect document page orientation/rotation (0, 90, 180, or 270 degrees).

    Uses bounding box aspect ratios of connected components (words/lines) to
    differentiate horizontal text layouts from vertical ones.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

    # Dilate horizontally to merge characters into text lines
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
    dilated = cv2.dilate(thresh, kernel, iterations=2)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    horiz_count = 0
    vert_count = 0

    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w > 30 and h > 10:
            if w > h * 1.5:
                horiz_count += 1
            elif h > w * 1.5:
                vert_count += 1

    # If vertical blocks are dominant, the page is likely rotated 90 or 270 degrees.
    if vert_count > horiz_count:
        # Default correction is 90 degrees clockwise rotation.
        return 90
    return 0

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

            for page_num in range(len(doc)):
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
            return out_bytes, avg_angle / len(doc) if len(doc) > 0 else 0.0

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
