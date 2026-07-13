import pytest
import cv2
import numpy as np
from io import BytesIO
from reportlab.pdfgen import canvas
from agentcore.services.idp.ocr import run_paddle_ocr

@pytest.fixture
def anyio_backend():
    return "asyncio"

def create_simple_pdf_bytes(text: str) -> bytes:
    """Dynamically generate a simple single-page PDF with native text."""
    buffer = BytesIO()
    p = canvas.Canvas(buffer)
    p.drawString(100, 100, text)
    p.showPage()
    p.save()
    return buffer.getvalue()

def create_simple_image_bytes(text: str) -> bytes:
    """Dynamically generate a simple PNG image containing drawn text."""
    img = np.ones((100, 300, 3), dtype=np.uint8) * 255
    cv2.putText(img, text, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    _, encoded = cv2.imencode(".png", img)
    return encoded.tobytes()

@pytest.mark.anyio
async def test_run_paddle_ocr_pdf():
    target_text = "Hello IDP Native Text"
    pdf_bytes = create_simple_pdf_bytes(target_text)
    
    # Run OCR on the PDF bytes (will use native text fallback or paddleocr)
    results = await run_paddle_ocr(pdf_bytes, "pdf", lang="en")
    
    assert len(results) > 0
    # At least one token/word should match or be part of target_text
    found = any(token["text"].lower() in target_text.lower() for token in results)
    assert found, f"Could not find '{target_text}' in OCR tokens: {results}"
    
    # Validate structure
    for token in results:
        assert "text" in token
        assert "bounding_box" in token
        assert len(token["bounding_box"]) == 4
        assert "confidence" in token
        assert "page_number" in token
        assert token["page_number"] == 1

@pytest.mark.anyio
async def test_run_paddle_ocr_image():
    target_text = "TestOCR"
    img_bytes = create_simple_image_bytes(target_text)
    
    results = await run_paddle_ocr(img_bytes, "png", lang="en")
    
    assert len(results) > 0
    # Validate structure of output
    for token in results:
        assert "text" in token
        assert "bounding_box" in token
        assert len(token["bounding_box"]) == 4
        assert "confidence" in token
        assert "page_number" in token
        assert token["page_number"] == 1


# ── _dominant_page_image: OCR a scanned page from the embedded image, not a low-DPI page raster ──
# Regression: a 720x1600 Aadhaar photo read perfectly as a raw .jpeg ("Manish Kumar") but became garbage
# ("SRUMALE") once merged into a PDF, because the PDF path rendered the page at 100 DPI and capped to
# 1000px — handing OCR a fraction of the pixels the embedded image already had.
def _pdf_with_full_page_image(w: int, h: int) -> bytes:
    """A single-page PDF whose one image (w×h) covers the whole page."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=w, height=h)
    img = (np.ones((h, w, 3), dtype=np.uint8) * 255)
    cv2.putText(img, "NATIVE", (10, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 5)
    ok, enc = cv2.imencode(".png", img)
    page.insert_image(page.rect, stream=enc.tobytes())
    out = doc.tobytes()
    doc.close()
    return out


def _pdf_with_small_logo(page_w: int, page_h: int, logo: int) -> bytes:
    """A single-page PDF with mostly text and one small logo image (does NOT dominate the page)."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    page.insert_text((50, 50), "This is a text page with a small logo in the corner.")
    img = np.zeros((logo, logo, 3), dtype=np.uint8)
    ok, enc = cv2.imencode(".png", img)
    page.insert_image(fitz.Rect(0, 0, logo, logo), stream=enc.tobytes())
    out = doc.tobytes()
    doc.close()
    return out


def test_dominant_image_returns_native_pixels_for_a_full_page_image():
    import fitz

    from agentcore.services.idp.ocr import _dominant_page_image

    pdf = _pdf_with_full_page_image(720, 1600)
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        img = _dominant_page_image(doc, doc[0])
    finally:
        doc.close()
    assert img is not None, "a full-page embedded image must be OCR'd from its own pixels"
    # The native image (720x1600) — NOT a page raster whose long side the pipeline would cap differently.
    assert max(img.shape[:2]) == 1600, "must return the embedded image at its NATIVE resolution"


def test_dominant_image_is_skipped_for_a_text_page():
    import fitz

    from agentcore.services.idp.ocr import _dominant_page_image

    doc = fitz.open(stream=create_simple_pdf_bytes("just native text, no image"), filetype="pdf")
    try:
        assert _dominant_page_image(doc, doc[0]) is None, "a text/vector page must be rendered, not image-extracted"
    finally:
        doc.close()


def test_dominant_image_is_skipped_for_a_small_logo():
    import fitz

    from agentcore.services.idp.ocr import _dominant_page_image

    doc = fitz.open(stream=_pdf_with_small_logo(600, 800, 60), filetype="pdf")
    try:
        # A 60px logo on a 600x800 page covers <1% — must fall back to the page render, not OCR the logo.
        assert _dominant_page_image(doc, doc[0]) is None
    finally:
        doc.close()


def test_dominant_image_is_skipped_for_a_tiny_low_res_image():
    """A low-res image stretched across a page gains nothing from native pixels — render (upscale) instead."""
    import fitz

    from agentcore.services.idp.ocr import _dominant_page_image

    pdf = _pdf_with_full_page_image(120, 160)  # dominates the page, but far below _MIN_NATIVE_OCR_SIDE
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        assert _dominant_page_image(doc, doc[0]) is None
    finally:
        doc.close()
