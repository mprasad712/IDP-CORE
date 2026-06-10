import pytest
import io
from PIL import Image, ImageDraw

from agentcore.services.idp.visual_detection import detect_visual_elements, PYZBAR_AVAILABLE, OPENCV_AVAILABLE


@pytest.fixture
def anyio_backend():
    return "asyncio"


def create_test_checkbox_image():
    """Create an image containing a drawn square simulating a checkbox."""
    # 200x200 image
    img = Image.new("RGB", (200, 200), "white")
    draw = ImageDraw.Draw(img)
    # Draw a checkbox border at x=20, y=20, w=30, h=30
    draw.rectangle([20, 20, 50, 50], outline="black", width=2)
    # Draw a line inside to make it checked
    draw.line([25, 25, 45, 45], fill="black", width=2)
    
    # Save as PNG
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    return img_bytes.getvalue()


@pytest.mark.anyio
async def test_detect_visual_elements_image():
    # 1. Create a dummy image
    img_bytes = create_test_checkbox_image()
    
    # 2. Run detector
    elements = await detect_visual_elements(img_bytes, "png")
    
    # 3. Assert outputs
    assert isinstance(elements, list)
    if OPENCV_AVAILABLE:
        # Checkbox should be detected
        checkboxes = [e for e in elements if e["element_type"] == "checkbox"]
        assert len(checkboxes) >= 1
        cb = checkboxes[0]
        assert cb["page_number"] == 1
        assert "x_min" in cb["bounding_box"]
        assert cb["confidence"] >= 0.0
        assert cb["decoded_value"] in ("checked", "unchecked")
    else:
        assert len(elements) == 0


@pytest.mark.anyio
async def test_detect_visual_elements_pyzbar_fallback():
    # Verify that even when PYZBAR is missing, the code doesn't raise error on execution
    img_bytes = create_test_checkbox_image()
    
    # Simulate PYZBAR being unavailable (monkeypatch the module check)
    import agentcore.services.idp.visual_detection as vd
    original_pyzbar_state = vd.PYZBAR_AVAILABLE
    vd.PYZBAR_AVAILABLE = False
    
    try:
        elements = await detect_visual_elements(img_bytes, "png")
        assert isinstance(elements, list)
    finally:
        vd.PYZBAR_AVAILABLE = original_pyzbar_state
