import cv2
import numpy as np
import fitz
from loguru import logger

_paddle_ocr_available = False
try:
    from paddleocr import PaddleOCR
    _paddle_ocr_available = True
except ImportError:
    logger.warning("[OCR] paddleocr package not found. Will use fallback OCR.")
except Exception as e:
    logger.warning(f"[OCR] Failed to import paddleocr: {e}. Will use fallback OCR.")

# Cache PaddleOCR instances per language
_ocr_instances = {}

def get_ocr_instance(lang: str):
    global _paddle_ocr_available, _ocr_instances
    if not _paddle_ocr_available:
        return None

    ocr_lang = "en"
    lang_clean = lang.lower().strip()
    if lang_clean in ("hi", "hindi", "mixed"):
        ocr_lang = "hi"

    if ocr_lang not in _ocr_instances:
        try:
            # use_angle_cls=True to handle text lines direction, show_log=False to reduce logging noise
            _ocr_instances[ocr_lang] = PaddleOCR(use_angle_cls=True, lang=ocr_lang, show_log=False)
            logger.info(f"[OCR] Initialized PaddleOCR model for language: {ocr_lang}")
        except Exception as e:
            logger.error(f"[OCR] Failed to initialize PaddleOCR for language {ocr_lang}: {e}")
            return None

    return _ocr_instances[ocr_lang]

async def run_paddle_ocr(file_bytes: bytes, file_type: str, lang: str = "en") -> list[dict]:
    """Run PaddleOCR on the given file bytes.

    Supports PDF and image formats (PNG, JPG, JPEG, TIFF, BMP, etc.).
    Returns a list of extracted tokens:
      [{'text': str, 'bounding_box': [[x0, y0], [x1, y1], [x2, y2], [x3, y3]], 'confidence': float, 'page_number': int}]
    """
    file_type = file_type.lower().strip(".")
    is_pdf = (file_type == "pdf")

    # Attempt to load and run PaddleOCR
    ocr_model = get_ocr_instance(lang)

    if ocr_model is not None:
        try:
            results = []
            if is_pdf:
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    pix = page.get_pixmap(dpi=150)
                    page_img_bytes = pix.tobytes("png")

                    nparr = np.frombuffer(page_img_bytes, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                    if img is None:
                        continue

                    # PaddleOCR expects a numpy array or image file path
                    ocr_res = ocr_model.ocr(img, cls=True)
                    if ocr_res and ocr_res[0]:
                        for line in ocr_res[0]:
                            box = line[0]
                            text, conf = line[1]
                            # box structure is: [[x0, y0], [x1, y1], [x2, y2], [x3, y3]]
                            results.append({
                                "text": text,
                                "bounding_box": box,
                                "confidence": float(conf),
                                "page_number": page_num + 1
                            })
                doc.close()
            else:
                nparr = np.frombuffer(file_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    ocr_res = ocr_model.ocr(img, cls=True)
                    if ocr_res and ocr_res[0]:
                        for line in ocr_res[0]:
                            box = line[0]
                            text, conf = line[1]
                            results.append({
                                "text": text,
                                "bounding_box": box,
                                "confidence": float(conf),
                                "page_number": 1
                            })
            return results
        except Exception as e:
            logger.warning(f"[OCR] PaddleOCR execution failed: {e}. Falling back to text/mock extraction.")

    # Fallback to PyMuPDF word extraction for PDFs, and basic mock for images
    results = []
    if is_pdf:
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page_num in range(len(doc)):
                page = doc[page_num]
                words = page.get_text("words")
                if words:
                    for w in words:
                        x0, y0, x1, y1, word_text, block_no, line_no, word_no = w
                        results.append({
                            "text": word_text,
                            "bounding_box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                            "confidence": 1.0,
                            "page_number": page_num + 1
                        })
            doc.close()
            return results
        except Exception as e:
            logger.error(f"[OCR] PDF native extraction fallback failed: {e}")
            return []
    else:
        # Fallback mock for pure image formats
        try:
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            h, w = (100, 100)
            if img is not None:
                h, w = img.shape[:2]
            return [{
                "text": "Scanned Image (Fallback)",
                "bounding_box": [[0.0, 0.0], [float(w), 0.0], [float(w), float(h)], [0.0, float(h)]],
                "confidence": 0.8,
                "page_number": 1
            }]
        except Exception as e:
            logger.error(f"[OCR] Image fallback failed: {e}")
            return []
