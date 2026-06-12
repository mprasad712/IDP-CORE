"""PaddleOCR wrapper for the IDP pipeline.

PDF pages are rendered to images via pdf2image (poppler) and OCR'd with
PaddleOCR — all pages are treated as scanned (no digital/scanned heuristic).

Spreadsheet and Word files are extracted natively (no OCR needed).
"""

import cv2
import numpy as np
from loguru import logger

_paddle_ocr_available = False
try:
    from paddleocr import PaddleOCR
    _paddle_ocr_available = True
except ImportError:
    logger.warning("[OCR] paddleocr not found. Will use fallback.")
except Exception as e:
    logger.warning(f"[OCR] Failed to import paddleocr: {e}. Will use fallback.")

# Cache PaddleOCR instances per language
_ocr_instances: dict = {}


def get_ocr_instance(lang: str):
    global _paddle_ocr_available, _ocr_instances
    if not _paddle_ocr_available:
        return None

    ocr_lang = "hi" if lang.lower().strip() in ("hi", "hindi", "mixed") else "en"

    if ocr_lang not in _ocr_instances:
        try:
            _ocr_instances[ocr_lang] = PaddleOCR(use_angle_cls=True, lang=ocr_lang, show_log=False)
            logger.info(f"[OCR] Initialized PaddleOCR for language: {ocr_lang}")
        except Exception as e:
            logger.error(f"[OCR] Failed to initialize PaddleOCR ({ocr_lang}): {e}")
            return None

    return _ocr_instances[ocr_lang]


# ─────────────────────────────────────────────────────────────────────────────
# Office-format native extraction (no OCR)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_spreadsheet_text(file_bytes: bytes) -> list[dict]:
    import io
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as e:
        logger.error(f"[OCR] Failed to read spreadsheet: {e}")
        return []
    results = []
    try:
        for sheet_index, ws in enumerate(wb.worksheets, start=1):
            for row in ws.iter_rows():
                for cell in row:
                    val = cell.value
                    if val is None or str(val).strip() == "":
                        continue
                    results.append({
                        "text": str(val).strip(),
                        "bounding_box": None,
                        "confidence": 1.0,
                        "page_number": sheet_index,
                    })
    finally:
        wb.close()
    return results


def _extract_docx_text(file_bytes: bytes) -> list[dict]:
    import io
    try:
        import docx
        document = docx.Document(io.BytesIO(file_bytes))
    except Exception as e:
        logger.error(f"[OCR] Failed to read docx: {e}")
        return []
    results = []
    for para in document.paragraphs:
        text = (para.text or "").strip()
        if text:
            results.append({"text": text, "bounding_box": None, "confidence": 1.0, "page_number": 1})
    for table in document.tables:
        for table_row in table.rows:
            for cell in table_row.cells:
                text = (cell.text or "").strip()
                if text:
                    results.append({"text": text, "bounding_box": None, "confidence": 1.0, "page_number": 1})
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Core OCR helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ocr_image(img: np.ndarray, ocr_model, page_number: int) -> list[dict]:
    """Run PaddleOCR on a numpy BGR image and return token dicts."""
    results = []
    try:
        ocr_res = ocr_model.ocr(img, cls=True)
        if ocr_res and ocr_res[0]:
            for line in ocr_res[0]:
                box = line[0]       # [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]
                text, conf = line[1]
                results.append({
                    "text": text,
                    "bounding_box": box,
                    "confidence": float(conf),
                    "page_number": page_number,
                })
    except Exception as e:
        logger.warning(f"[OCR] PaddleOCR failed on page {page_number}: {e}")
    return results


def _pdf_to_images(file_bytes: bytes) -> list:
    """Convert PDF bytes to a list of PIL Images using pdf2image (poppler)."""
    try:
        from pdf2image import convert_from_bytes
        return convert_from_bytes(file_bytes)
    except Exception as e:
        logger.warning(f"[OCR] pdf2image failed ({e}), falling back to fitz pixmap")
        import fitz
        images = []
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            png_bytes = pix.tobytes("png")
            nparr = np.frombuffer(png_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                images.append(img)
        doc.close()
        return images


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def run_paddle_ocr(file_bytes: bytes, file_type: str, lang: str = "en") -> list[dict]:
    """OCR ``file_bytes`` and return a flat list of word tokens.

    Each token: ``{text, bounding_box, confidence, page_number}``

    - PDF: all pages rendered via pdf2image → PaddleOCR (no scanned/digital check)
    - Images: PaddleOCR directly
    - XLSX/DOCX: native text extraction, no OCR
    """
    file_type = file_type.lower().strip(".")

    if file_type in ("xlsx", "xls"):
        return _extract_spreadsheet_text(file_bytes)
    if file_type == "docx":
        return _extract_docx_text(file_bytes)

    ocr_model = get_ocr_instance(lang)

    # ── PDF ─────────────────────────────────────────────────────────────────
    if file_type == "pdf":
        if ocr_model is not None:
            try:
                pil_images = _pdf_to_images(file_bytes)
                results = []
                for page_num, img_source in enumerate(pil_images, start=1):
                    # Accept both PIL Image (from pdf2image) and numpy array (fitz fallback)
                    if hasattr(img_source, "mode"):
                        img = cv2.cvtColor(np.array(img_source), cv2.COLOR_RGB2BGR)
                    else:
                        img = img_source
                    results.extend(_ocr_image(img, ocr_model, page_num))
                logger.info(f"[OCR] PaddleOCR extracted {len(results)} tokens from {len(pil_images)} PDF page(s)")
                return results
            except Exception as e:
                logger.warning(f"[OCR] PDF PaddleOCR path failed: {e}. Falling back to native text.")

        # Fallback: PyMuPDF native word extraction
        import fitz
        results = []
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page_num in range(len(doc)):
                for w in doc[page_num].get_text("words"):
                    x0, y0, x1, y1, word_text, *_ = w
                    results.append({
                        "text": word_text,
                        "bounding_box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                        "confidence": 1.0,
                        "page_number": page_num + 1,
                    })
            doc.close()
        except Exception as e:
            logger.error(f"[OCR] PDF native fallback failed: {e}")
        return results

    # ── Image ────────────────────────────────────────────────────────────────
    if ocr_model is not None:
        try:
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                return _ocr_image(img, ocr_model, page_number=1)
        except Exception as e:
            logger.warning(f"[OCR] Image PaddleOCR failed: {e}")

    # Mock fallback for images when PaddleOCR unavailable
    try:
        nparr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h, w = (img.shape[:2] if img is not None else (100, 100))
        return [{
            "text": "Scanned Image (Fallback)",
            "bounding_box": [[0.0, 0.0], [float(w), 0.0], [float(w), float(h)], [0.0, float(h)]],
            "confidence": 0.8,
            "page_number": 1,
        }]
    except Exception as e:
        logger.error(f"[OCR] Image fallback failed: {e}")
        return []
