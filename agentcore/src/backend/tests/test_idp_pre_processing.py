import pytest
import cv2
import numpy as np
from agentcore.services.idp.pre_processing import (
    deskew_image,
    detect_rotation_angle,
    rotate_image,
    detect_and_correct_skew,
    detect_and_correct_rotation
)

@pytest.fixture
def anyio_backend():
    return "asyncio"

def create_skewed_text_image(angle_deg: float) -> np.ndarray:
    """Create a white background image with a black rectangle rotated by angle_deg."""
    img = np.ones((400, 400, 3), dtype=np.uint8) * 255
    # Draw a black line/rectangle at a certain angle
    center = (200, 200)
    size = (300, 50)
    rect = (center, size, angle_deg)
    box = cv2.boxPoints(rect)
    box = box.astype(np.int64)
    cv2.drawContours(img, [box], 0, (0, 0, 0), -1)
    return img

def create_rotated_text_image(is_vertical: bool) -> np.ndarray:
    """Create an image representing either horizontal lines or vertical lines of text."""
    img = np.ones((400, 400, 3), dtype=np.uint8) * 255
    if is_vertical:
        # Draw a vertical black bar representing vertical text lines (e.g. rotated 90 deg)
        cv2.rectangle(img, (180, 50), (220, 350), (0, 0, 0), -1)
    else:
        # Draw a horizontal black bar representing standard text lines
        cv2.rectangle(img, (50, 180), (350, 220), (0, 0, 0), -1)
    return img

def test_deskew_image():
    # 1. Test image with no skew
    img_flat = create_skewed_text_image(0.0)
    _, angle_flat = deskew_image(img_flat)
    assert abs(angle_flat) < 0.5

    # 2. Test image with minor skew
    skew_angle = -15.0
    img_skewed = create_skewed_text_image(skew_angle)
    corrected, angle_detected = deskew_image(img_skewed)
    
    # Check that it detected a skew angle close to our target skew
    assert abs(angle_detected - skew_angle) < 5.0
    assert corrected.shape == img_skewed.shape

def test_detect_rotation_angle():
    # Horizontal layout -> correction is 0 or 180 (180 only resolvable when OCR is available).
    img_horiz = create_rotated_text_image(is_vertical=False)
    assert detect_rotation_angle(img_horiz) in (0, 180)

    # Vertical layout -> correction is 90 or 270.
    img_vert = create_rotated_text_image(is_vertical=True)
    assert detect_rotation_angle(img_vert) in (90, 270)


def test_detect_rotation_angle_delegates_to_orientation_model(monkeypatch):
    """detect_rotation_angle delegates to the doc-orientation classifier (predict_orientation) and
    returns whatever orientation it reports — e.g. an upside-down page resolves to 180.

    (The earlier OCR rotate-and-score flip heuristic + its `_ocr_text_score` helper were replaced by
    the ONNX PP-LCNet_x1_0_doc_ori model with an OpenCV fallback, so this verifies the current
    delegation rather than the removed helper the old test monkeypatched.)"""
    from agentcore.services.idp import pre_processing

    img = create_rotated_text_image(is_vertical=False)
    monkeypatch.setattr(pre_processing, "predict_orientation", lambda image: 180)
    assert pre_processing.detect_rotation_angle(img) == 180
    monkeypatch.setattr(pre_processing, "predict_orientation", lambda image: 0)
    assert pre_processing.detect_rotation_angle(img) == 0

def test_rotate_image():
    img = np.ones((100, 200, 3), dtype=np.uint8) * 255
    
    # 90 degrees rotation should swap dimensions
    rotated_90 = rotate_image(img, 90)
    assert rotated_90.shape[:2] == (200, 100)
    
    # 180 degrees keeps same dimensions
    rotated_180 = rotate_image(img, 180)
    assert rotated_180.shape[:2] == (100, 200)

@pytest.mark.anyio
async def test_detect_and_correct_skew_image():
    img = create_skewed_text_image(-10.0)
    _, encoded = cv2.imencode(".png", img)
    img_bytes = encoded.tobytes()

    corrected_bytes, angle = await detect_and_correct_skew(img_bytes, "png")
    assert abs(angle - (-10.0)) < 5.0
    assert len(corrected_bytes) > 0

@pytest.mark.anyio
async def test_detect_and_correct_rotation_image():
    img = create_rotated_text_image(is_vertical=True)
    _, encoded = cv2.imencode(".png", img)
    img_bytes = encoded.tobytes()

    corrected_bytes, angle = await detect_and_correct_rotation(img_bytes, "png")
    assert angle == 90
    assert len(corrected_bytes) > 0
