from __future__ import annotations

import io
import numpy as np
from PIL import Image
from loguru import logger

try:
    import cv2
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False

try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False


async def detect_visual_elements(file_bytes: bytes, file_type: str) -> list[dict]:
    """Detect visual elements in a document page by page.

    Visual elements include signatures, stamps, checkboxes, QR codes, barcodes,
    logos, and annotations.

    Args:
        file_bytes: The raw document file content.
        file_type: File extension (e.g., 'pdf', 'png', 'jpg').

    Returns:
        A list of dictionaries representing detected elements:
        {
            "element_type": str,
            "page_number": int,
            "bounding_box": {"x_min": float, "y_min": float, "x_max": float, "y_max": float},
            "confidence": float,
            "decoded_value": str | None
        }
    """
    detected_elements = []

    if file_type.lower() == "pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page_idx in range(len(doc)):
                page_number = page_idx + 1
                page = doc[page_idx]
                pix = page.get_pixmap()
                page_img_bytes = pix.tobytes("png")
                
                # Run detectors on this page
                elements = _detect_on_page_image(page_img_bytes, page_number)
                detected_elements.extend(elements)
        except Exception as e:
            logger.error(f"Error extracting pages from PDF for visual detection: {e}")
    else:
        # Treat as a single-page image
        elements = _detect_on_page_image(file_bytes, 1)
        detected_elements.extend(elements)

    return detected_elements


def _detect_on_page_image(image_bytes: bytes, page_number: int) -> list[dict]:
    """Run barcode, QR, and contour detectors on a single page image."""
    elements = []
    
    # 1. Barcode and QR Code Reader via pyzbar
    if PYZBAR_AVAILABLE:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            decoded_objects = pyzbar.decode(img)
            width, height = img.size
            for obj in decoded_objects:
                # Get normalized bounding box
                x, y, w, h = obj.rect
                bbox = {
                    "x_min": max(0.0, float(x) / width),
                    "y_min": max(0.0, float(y) / height),
                    "x_max": min(1.0, float(x + w) / width),
                    "y_max": min(1.0, float(y + h) / height)
                }
                element_type = "qr" if obj.type == "QRCODE" else "barcode"
                
                try:
                    val = obj.data.decode("utf-8")
                except Exception:
                    val = str(obj.data)

                elements.append({
                    "element_type": element_type,
                    "page_number": page_number,
                    "bounding_box": bbox,
                    "confidence": 1.0,
                    "decoded_value": val
                })
        except Exception as e:
            logger.warning(f"Error running pyzbar barcode detection: {e}")
    else:
        logger.debug("pyzbar is not installed. Barcode/QR detection skipped.")

    # 2. Heuristic Checkbox and Signature Detector via OpenCV
    if OPENCV_AVAILABLE:
        try:
            # Convert bytes to numpy array for OpenCV
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                height, width = img.shape[:2]
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                
                # Checkbox Heuristic Detection
                # Threshold to get binary image
                _, thresh = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)
                # Find contours
                contours, _ = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
                
                for cnt in contours:
                    approx = cv2.approxPolyDP(cnt, 0.04 * cv2.arcLength(cnt, True), True)
                    # Checkboxes are small squares/rectangles
                    if len(approx) == 4:
                        x, y, w, h = cv2.boundingRect(approx)
                        aspect_ratio = float(w) / h
                        # Typical checkbox size limit
                        if 10 < w < 80 and 10 < h < 80 and 0.85 < aspect_ratio < 1.15:
                            # Evaluate checkbox fill status (confidence represents fill ratio)
                            roi = thresh[y:y+h, x:x+w]
                            fill_ratio = float(cv2.countNonZero(roi)) / (w * h)
                            
                            bbox = {
                                "x_min": float(x) / width,
                                "y_min": float(y) / height,
                                "x_max": float(x + w) / width,
                                "y_max": float(y + h) / height
                            }
                            # If filled, it is checked
                            decoded_val = "checked" if fill_ratio > 0.15 else "unchecked"
                            elements.append({
                                "element_type": "checkbox",
                                "page_number": page_number,
                                "bounding_box": bbox,
                                "confidence": round(fill_ratio, 4),
                                "decoded_value": decoded_val
                            })
                            
                # Signature Heuristic Detection (Look for large clusters of blue/black stroke contours)
                # This is a fallback mockup detector for signature lines
                # Usually signatures are placed near the bottom-right or bottom-left
                for cnt in contours:
                    x, y, w, h = cv2.boundingRect(cnt)
                    # Look for non-regular aspect ratio and larger contour signature stroke patterns
                    if w > 80 and h > 30 and w < 300 and h < 150:
                        # Check proximity to bottom (often where signatures reside)
                        if y > int(height * 0.6):
                            bbox = {
                                "x_min": float(x) / width,
                                "y_min": float(y) / height,
                                "x_max": float(x + w) / width,
                                "y_max": float(y + h) / height
                            }
                            elements.append({
                                "element_type": "signature",
                                "page_number": page_number,
                                "bounding_box": bbox,
                                "confidence": 0.85,
                                "decoded_value": None
                            })
                            break # Limit to first candidate signature contour per page for this stub
        except Exception as e:
            logger.warning(f"Error running OpenCV visual element detection: {e}")
    else:
        logger.debug("OpenCV is not installed. Heuristic visual element detection skipped.")

    return elements
