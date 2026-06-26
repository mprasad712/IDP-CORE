import io
import pytest
from uuid import UUID, uuid4
from fastapi.testclient import TestClient
from sqlmodel import select

from agentcore.main import create_app
from agentcore.services.deps import session_scope, get_storage_service
from agentcore.services.auth.utils import get_current_active_user
from agentcore.api.idp import idp_rbac, _idp_submit_rbac
from agentcore.services.database.models.user.model import User
from agentcore.services.database.models.organization.model import Organization
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.database.models.idp.config import IdpAgent, IdpFieldConfiguration
from agentcore.services.database.models.idp.documents import IdpDocument
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.project.model import Project
from agentcore.services.database.models.role.model import Role

mock_user = None

def get_mock_user():
    global mock_user
    return mock_user

@pytest.fixture
def anyio_backend():
    return "asyncio"

# ... Setup data fixture stays the same ...
# (skipping setup_test_data block as it is not modified)


@pytest.fixture
async def setup_test_data():
    """Sets up user, organization, base agent, and IDP agent config."""
    async with session_scope() as session:
        # Resolve developer role
        developer_role = (await session.exec(select(Role).where(Role.name == "idp_configurator"))).first()

        # Create user
        unique_suffix = uuid4().hex[:8]
        user = User(
            id=uuid4(),
            username=f"doc_test_user_{unique_suffix}@test.com",
            email=f"doc_test_user_{unique_suffix}@test.com",
            password="testpassword",
            is_active=True,
            is_superuser=False,
            role="idp_configurator",
        )
        session.add(user)
        await session.flush()

        # Create organization
        org = Organization(
            id=uuid4(),
            name=f"Document Test Org {unique_suffix}",
            owner_user_id=user.id,
            created_by=user.id,
        )
        session.add(org)
        await session.commit()
        await session.refresh(user)
        await session.refresh(org)

        # Create membership
        membership = UserOrganizationMembership(
            id=uuid4(),
            user_id=user.id,
            org_id=org.id,
            status="active",
            role_id=developer_role.id,
        )
        session.add(membership)

        # Create project
        proj = Project(id=uuid4(), name="Document Test Project", user_id=user.id)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)

        # Create base agent
        base_agent = Agent(
            id=uuid4(),
            name="Test Base Agent",
            user_id=user.id,
            project_id=proj.id,
            org_id=org.id,
            data={},
        )
        session.add(base_agent)
        await session.commit()
        await session.refresh(base_agent)

        # Create IDP Agent Config
        idp_agent = IdpAgent(
            id=uuid4(),
            agent_id=base_agent.id,
            extraction_mode="dynamic_prompting",
        )
        session.add(idp_agent)
        await session.commit()
        await session.refresh(idp_agent)

        yield {
            "user": user,
            "org": org,
            "base_agent": base_agent,
            "idp_agent": idp_agent,
        }

        # Cleanup test data after test runs
        async with session_scope() as cleanup_session:
            # Delete uploaded documents
            docs = (await cleanup_session.exec(select(IdpDocument).where(IdpDocument.agent_id == idp_agent.id))).all()
            for d in docs:
                await cleanup_session.delete(d)

            # Delete configurations, agents, projects, membership, org, user
            db_idp_agent = await cleanup_session.get(IdpAgent, idp_agent.id)
            if db_idp_agent:
                await cleanup_session.delete(db_idp_agent)

            db_base_agent = await cleanup_session.get(Agent, base_agent.id)
            if db_base_agent:
                await cleanup_session.delete(db_base_agent)

            db_proj = await cleanup_session.get(Project, proj.id)
            if db_proj:
                await cleanup_session.delete(db_proj)

            m_rows = (await cleanup_session.exec(select(UserOrganizationMembership).where(UserOrganizationMembership.user_id == user.id))).all()
            for m in m_rows:
                await cleanup_session.delete(m)

            db_org = await cleanup_session.get(Organization, org.id)
            if db_org:
                await cleanup_session.delete(db_org)

            db_user = await cleanup_session.get(User, user.id)
            if db_user:
                await cleanup_session.delete(db_user)

            await cleanup_session.commit()

@pytest.mark.anyio
async def test_document_upload_flow(setup_test_data):
    global mock_user
    data = setup_test_data
    user = data["user"]
    idp_agent = data["idp_agent"]

    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    app.dependency_overrides[idp_rbac] = get_mock_user
    app.dependency_overrides[_idp_submit_rbac] = get_mock_user

    class MockStorageService:
        async def save_file(self, agent_id: str, file_name: str, data: bytes) -> None:
            pass

    app.dependency_overrides[get_storage_service] = lambda: MockStorageService()
    client = TestClient(app)

    # Set mock user
    mock_user = user

    # 1. Test upload with invalid agent_id
    response = client.post(
        "/api/v1/idp/documents/upload",
        data={"agent_id": str(uuid4())},
        files=[("files", ("test.pdf", b"pdf content", "application/pdf"))]
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]

    # 2. Test upload with invalid file extension (.exe is not an allowed document type;
    #    .txt is now allowed, so use a genuinely-disallowed extension here)
    response = client.post(
        "/api/v1/idp/documents/upload",
        data={"agent_id": str(idp_agent.id)},
        files=[("files", ("test.exe", b"binary content", "application/octet-stream"))]
    )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]

    # 3. Test successful upload (single file)
    pdf_content = b"%PDF-1.4 mock pdf content"
    response = client.post(
        "/api/v1/idp/documents/upload",
        data={"agent_id": str(idp_agent.id)},
        files=[("files", ("invoice.pdf", pdf_content, "application/pdf"))]
    )
    assert response.status_code == 201
    res_json = response.json()
    assert "document_ids" in res_json
    assert len(res_json["document_ids"]) == 1
    doc_id = res_json["document_ids"][0]

    # Verify document is in database
    async with session_scope() as session:
        doc = await session.get(IdpDocument, UUID(doc_id))
        assert doc is not None
        assert doc.original_filename == "invoice.pdf"
        assert doc.status == "queued"
        assert doc.file_type == "pdf"
        assert doc.file_size_bytes == len(pdf_content)
        assert doc.uploaded_by == user.id

    # 4. Test successful bulk upload (multiple files)
    response = client.post(
        "/api/v1/idp/documents/upload",
        data={"agent_id": str(idp_agent.agent_id)}, # test mapping via base agent_id too
        files=[
            ("files", ("doc1.png", b"png content", "image/png")),
            ("files", ("doc2.jpg", b"jpg content", "image/jpeg"))
        ]
    )
    assert response.status_code == 201
    res_json = response.json()
    assert len(res_json["document_ids"]) == 2

    # Clean overrides
    app.dependency_overrides.clear()


# --- H1/H2: org-scope access helpers (upload + process/reprocess) --------------------
from types import SimpleNamespace as _SNS_h12
from uuid import uuid4 as _uuid_h12
import pytest as _pytest_h12
import agentcore.api.idp.documents as _docs_mod


@_pytest_h12.fixture
def anyio_backend():
    return "asyncio"


class _Res_h12:
    def __init__(self, val): self._val = val
    def first(self): return self._val


class _Sess_h12:
    """Fake session whose .exec(...).first() returns a fixed org_id (the doc/agent's org)."""
    def __init__(self, org_id): self._org_id = org_id
    async def exec(self, *a, **k): return _Res_h12(self._org_id)


@_pytest_h12.mark.anyio
async def test_h2_can_access_document_root_bypass(monkeypatch):
    async def _scope(s, u): return True, []
    monkeypatch.setattr(_docs_mod, "resolve_org_scope", _scope)
    doc = _SNS_h12(agent_id=_uuid_h12())
    assert await _docs_mod._can_access_document(_Sess_h12(None), _SNS_h12(id=_uuid_h12()), doc) is True


@_pytest_h12.mark.anyio
async def test_h2_can_access_document_same_org_allowed(monkeypatch):
    org = _uuid_h12()
    async def _scope(s, u): return False, [org]
    monkeypatch.setattr(_docs_mod, "resolve_org_scope", _scope)
    doc = _SNS_h12(agent_id=_uuid_h12())
    assert await _docs_mod._can_access_document(_Sess_h12(org), _SNS_h12(id=_uuid_h12()), doc) is True


@_pytest_h12.mark.anyio
async def test_h2_can_access_document_cross_org_denied(monkeypatch):
    async def _scope(s, u): return False, [_uuid_h12()]
    monkeypatch.setattr(_docs_mod, "resolve_org_scope", _scope)
    doc = _SNS_h12(agent_id=_uuid_h12())
    assert await _docs_mod._can_access_document(_Sess_h12(_uuid_h12()), _SNS_h12(id=_uuid_h12()), doc) is False


@_pytest_h12.mark.anyio
async def test_h1_can_access_idp_agent_cross_org_denied(monkeypatch):
    async def _scope(s, u): return False, [_uuid_h12()]
    monkeypatch.setattr(_docs_mod, "resolve_org_scope", _scope)
    agent = _SNS_h12(agent_id=_uuid_h12())
    assert await _docs_mod._can_access_idp_agent(_Sess_h12(_uuid_h12()), _SNS_h12(id=_uuid_h12()), agent) is False


@_pytest_h12.mark.anyio
async def test_h1_can_access_idp_agent_same_org_allowed(monkeypatch):
    org = _uuid_h12()
    async def _scope(s, u): return False, [org]
    monkeypatch.setattr(_docs_mod, "resolve_org_scope", _scope)
    agent = _SNS_h12(agent_id=_uuid_h12())
    assert await _docs_mod._can_access_idp_agent(_Sess_h12(org), _SNS_h12(id=_uuid_h12()), agent) is True
