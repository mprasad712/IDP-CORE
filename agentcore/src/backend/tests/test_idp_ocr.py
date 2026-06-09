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
