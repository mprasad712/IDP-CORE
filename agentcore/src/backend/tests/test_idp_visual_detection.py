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


@pytest.mark.anyio
async def test_detect_and_persist(monkeypatch):
    from uuid import uuid4
    from agentcore.services.deps import session_scope
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.database.models.idp.documents import IdpDocument, IdpDetectedElement
    from agentcore.services.idp.visual_detection import detect_and_persist
    from sqlmodel import select

    # 1. Create a dummy image
    img_bytes = create_test_checkbox_image()

    # 2. Setup document
    agent_id = uuid4()
    doc_id = uuid4()

    async with session_scope() as session:
        base_agent = Agent(id=agent_id, name="Test Visual Base Agent")
        session.add(base_agent)
        await session.flush()

        idp_agent = IdpAgent(id=agent_id, agent_id=agent_id, extraction_mode="dynamic_prompting")
        session.add(idp_agent)
        await session.flush()

        doc = IdpDocument(
            id=doc_id,
            agent_id=agent_id,
            original_filename="visual_test.png",
            file_path="mock/visual_test.png",
            file_type="png",
            file_size_bytes=len(img_bytes),
            source="upload",
            status="queued"
        )
        session.add(doc)
        await session.commit()

    try:
        # 3. Run detect_and_persist
        async with session_scope() as session:
            created_ids = await detect_and_persist(session, doc_id, img_bytes, "png", enabled_types={"checkbox"})
            
        # 4. Assert DB values
        async with session_scope() as session:
            db_elements = (await session.exec(select(IdpDetectedElement).where(IdpDetectedElement.document_id == doc_id))).all()
            if OPENCV_AVAILABLE:
                assert len(db_elements) >= 1
                assert db_elements[0].element_type == "checkbox"
                assert db_elements[0].id in created_ids
            else:
                assert len(db_elements) == 0
                assert len(created_ids) == 0
    finally:
        # Cleanup
        async with session_scope() as session:
            for el in (await session.exec(select(IdpDetectedElement).where(IdpDetectedElement.document_id == doc_id))).all():
                await session.delete(el)
            d = await session.get(IdpDocument, doc_id)
            if d: await session.delete(d)
            ia = await session.get(IdpAgent, agent_id)
            if ia: await session.delete(ia)
            ba = await session.get(Agent, agent_id)
            if ba: await session.delete(ba)
            await session.commit()
