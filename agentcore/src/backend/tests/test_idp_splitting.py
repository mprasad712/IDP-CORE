import pytest
from uuid import uuid4
import fitz
from sqlmodel import select

from agentcore.services.deps import session_scope
from agentcore.services.database.models.idp.documents import IdpDocument
from agentcore.services.database.models.idp.config import IdpAgent
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.idp.splitting import detect_document_boundaries, materialize_children


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_detect_document_boundaries():
    # Construct mock tokens
    tokens = [
        # Page 1 (Invoice)
        {"text": "Invoice", "page_number": 1},
        {"text": "INV-100", "page_number": 1},
        # Page 2 (Invoice page 2)
        {"text": "Subtotal 100", "page_number": 2},
        # Page 3 (Purchase Order)
        {"text": "Purchase", "page_number": 3},
        {"text": "Order", "page_number": 3},
        {"text": "PO-999", "page_number": 3},
        # Page 4 (Blank separator page - very short text)
        {"text": " ", "page_number": 4},
        # Page 5 (Tax Invoice / Page 1 Reset)
        {"text": "Tax Invoice", "page_number": 5},
        {"text": "Page 1", "page_number": 5},
    ]

    page_status = {"page_count": 5}
    boundaries = detect_document_boundaries(tokens, page_status)

    # Expected boundaries — a PARTITION of all pages (nothing is ever dropped):
    # 0 to 1 (pages 1-2)
    # 2 to 3 (page 3 PO + page 4 blank separator, which attaches to the segment it follows —
    #         a "blank" page may equally be an image-only scan, so it must never be discarded)
    # 4 to 4 (page 5 Tax Invoice)
    assert boundaries == [(0, 1), (2, 3), (4, 4)]
    # coverage invariant: every page belongs to exactly one segment
    covered = sorted(p for s, e in boundaries for p in range(s, e + 1))
    assert covered == list(range(5))


class MockStorage:
    def __init__(self, file_bytes):
        self.file_bytes = file_bytes
        self.saved_files = {}

    async def get_file(self, agent_id: str, file_name: str) -> bytes:
        return self.file_bytes

    async def save_file(self, agent_id: str, file_name: str, data: bytes) -> None:
        self.saved_files[file_name] = data


@pytest.mark.anyio
async def test_materialize_children():
    agent_id = uuid4()
    parent_doc_id = uuid4()

    # Create 3-page parent PDF bytes
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((50, 50), f"Parent Page {i+1}")
    pdf_bytes = doc.tobytes()
    doc.close()

    async with session_scope() as session:
        # Create base Agent
        base_agent = Agent(id=agent_id, name="Test Agent", data={})
        session.add(base_agent)
        await session.flush()

        # Create IDP Agent
        idp_agent = IdpAgent(id=agent_id, agent_id=agent_id, extraction_mode="dynamic_prompting")
        session.add(idp_agent)
        await session.flush()

        # Create parent Document
        parent_doc = IdpDocument(
            id=parent_doc_id,
            agent_id=agent_id,
            original_filename="parent.pdf",
            file_path="mock/parent.pdf",
            file_type="pdf",
            file_size_bytes=len(pdf_bytes),
            page_count=3,
            source="upload",
            status="extracted",
        )
        session.add(parent_doc)
        await session.commit()

        # Retrieve doc from session to ensure it's loaded
        db_parent = await session.get(IdpDocument, parent_doc_id)

    # Boundaries: Split into page 1 (0, 0) and pages 2-3 (1, 2)
    boundaries = [(0, 0), (1, 2)]
    mock_storage = MockStorage(pdf_bytes)

    try:
        async with session_scope() as session:
            # We must load parent_doc inside the session
            db_parent = await session.get(IdpDocument, parent_doc_id)
            child_ids = await materialize_children(session, mock_storage, db_parent, boundaries)

        assert len(child_ids) == 2
        assert len(mock_storage.saved_files) == 2

        # Verify child DB entries
        async with session_scope() as session:
            children = (await session.exec(
                select(IdpDocument).where(IdpDocument.parent_document_id == parent_doc_id)
            )).all()
            assert len(children) == 2
            
            # Map by start page
            children.sort(key=lambda d: d.original_filename)
            # child 1: page 1-1 (0, 0)
            c1 = children[0]
            assert c1.page_count == 1
            assert c1.status == "queued"
            assert c1.extra.get("multi_doc_split") is False
            
            # check split pdf file size
            c1_name = c1.file_path.split("/")[-1]
            assert c1_name in mock_storage.saved_files
            
            # child 2: pages 2-3 (1, 2)
            c2 = children[1]
            assert c2.page_count == 2
            assert c2.status == "queued"
            assert c2.extra.get("multi_doc_split") is False
            
            c2_name = c2.file_path.split("/")[-1]
            assert c2_name in mock_storage.saved_files

    finally:
        # Cleanup
        async with session_scope() as session:
            children = (await session.exec(
                select(IdpDocument).where(IdpDocument.parent_document_id == parent_doc_id)
            )).all()
            for c in children:
                await session.delete(c)
            db_parent = await session.get(IdpDocument, parent_doc_id)
            if db_parent: await session.delete(db_parent)
            db_ia = await session.get(IdpAgent, agent_id)
            if db_ia: await session.delete(db_ia)
            db_ba = await session.get(Agent, agent_id)
            if db_ba: await session.delete(db_ba)
            await session.commit()


@pytest.mark.anyio
async def test_processed_doc_detail_includes_split_children_summary():
    """A split parent's detail payload carries the children + aggregate counts (Playground follows them)."""
    from agentcore.api.idp.processed_docs import build_processed_doc_detail

    agent_id = uuid4()
    parent_doc_id = uuid4()
    child_specs = [
        ("bundle_split_1_1.pdf", "pending_review", 0.91),
        ("bundle_split_2_3.pdf", "skipped", None),
        ("bundle_split_4_4.pdf", "processing", None),
    ]

    try:
        async with session_scope() as session:
            session.add(Agent(id=agent_id, name="Split Detail Agent", data={}))
            await session.flush()
            session.add(IdpAgent(id=agent_id, agent_id=agent_id, extraction_mode="dynamic_prompting"))
            await session.flush()
            session.add(IdpDocument(
                id=parent_doc_id, agent_id=agent_id, original_filename="bundle.pdf",
                file_path="mock/bundle.pdf", file_type="pdf", file_size_bytes=10,
                source="upload", status="split",
            ))
            for fname, st, conf in child_specs:
                session.add(IdpDocument(
                    id=uuid4(), agent_id=agent_id, parent_document_id=parent_doc_id,
                    original_filename=fname, file_path=f"mock/{fname}", file_type="pdf",
                    file_size_bytes=5, source="upload", status=st, overall_confidence=conf,
                ))
            await session.commit()

        async with session_scope() as session:
            parent = await session.get(IdpDocument, parent_doc_id)
            detail = await build_processed_doc_detail(session, parent)

        assert detail.children_summary is not None
        assert detail.children_summary.total == 3
        assert detail.children_summary.done == 2          # pending_review + skipped; 'processing' is not done
        assert detail.children_summary.extracted == 1
        assert detail.children_summary.skipped == 1
        assert detail.children_summary.failed == 0
        assert [c.original_filename for c in detail.children] == [s[0] for s in child_specs]
        assert [c.page_range for c in detail.children] == ["1-1", "2-3", "4-4"]

        # Non-split docs must NOT pay the children query / carry the fields.
        async with session_scope() as session:
            for c_id in [c.id for c in detail.children]:
                child = await session.get(IdpDocument, c_id)
                child_detail = await build_processed_doc_detail(session, child)
                assert child_detail.children == []
                assert child_detail.children_summary is None
    finally:
        async with session_scope() as session:
            children = (await session.exec(
                select(IdpDocument).where(IdpDocument.parent_document_id == parent_doc_id)
            )).all()
            for c in children:
                await session.delete(c)
            db_parent = await session.get(IdpDocument, parent_doc_id)
            if db_parent: await session.delete(db_parent)
            db_ia = await session.get(IdpAgent, agent_id)
            if db_ia: await session.delete(db_ia)
            db_ba = await session.get(Agent, agent_id)
            if db_ba: await session.delete(db_ba)
            await session.commit()
