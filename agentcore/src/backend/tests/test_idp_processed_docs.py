import pytest
from uuid import UUID, uuid4
from fastapi.testclient import TestClient
from sqlmodel import select

from agentcore.main import create_app
from agentcore.services.deps import session_scope
from agentcore.services.auth.utils import get_current_active_user
from agentcore.api.idp import idp_rbac
from agentcore.services.database.models.user.model import User
from agentcore.services.database.models.organization.model import Organization
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.database.models.idp.config import IdpAgent, IdpFieldConfiguration, IdpReviewSession
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpProcessingJob,
    IdpExtractedHeader,
    IdpExtractedLineItem,
)
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.project.model import Project
from agentcore.services.database.models.role.model import Role

# Global test variables to control current user
mock_user = None

def get_mock_user():
    global mock_user
    return mock_user

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def setup_test_data():
    """Sets up user, organization, base agent, field config, document, job, and extraction details."""
    async with session_scope() as session:
        # Resolve developer role
        developer_role = (await session.exec(select(Role).where(Role.name == "developer"))).first()

        # Create user
        user = User(
            id=uuid4(),
            username="docs_test_user@test.com",
            email="docs_test_user@test.com",
            password="testpassword",
            is_active=True,
            is_superuser=False,
            role="developer",
        )
        session.add(user)
        await session.flush()

        # Create organization
        org = Organization(
            id=uuid4(),
            name="Docs Test Org",
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
        proj = Project(id=uuid4(), name="Docs Test Project", user_id=user.id)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)

        # Create base agent
        base_agent = Agent(
            id=uuid4(),
            name="Docs Base Agent",
            user_id=user.id,
            project_id=proj.id,
            org_id=org.id,
            data={},
        )
        session.add(base_agent)
        await session.commit()
        await session.refresh(base_agent)

        # Create IDP agent
        idp_agent = IdpAgent(
            id=uuid4(),
            agent_id=base_agent.id,
            extraction_mode="dynamic_prompting",
            default_rule_action="pending_review",
            is_active=True,
        )
        session.add(idp_agent)
        await session.commit()
        await session.refresh(idp_agent)

        # Create IDP Document
        doc = IdpDocument(
            id=uuid4(),
            agent_id=idp_agent.id,
            original_filename="invoice_receipt.pdf",
            file_path="mock/path/invoice_receipt.pdf",
            file_type="pdf",
            file_size_bytes=4096,
            source="upload",
            status="pending_review",
            overall_confidence=0.7500,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        # Create Job
        job = IdpProcessingJob(
            id=uuid4(),
            document_id=doc.id,
            agent_id=idp_agent.id,
            status="completed",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        # Create extracted headers
        header_1 = IdpExtractedHeader(
            id=uuid4(),
            document_id=doc.id,
            job_id=job.id,
            field_name="vendor_name",
            extracted_value="Tesco Corp",
            confidence_score=0.8500,
            is_reviewed=False,
        )
        header_2 = IdpExtractedHeader(
            id=uuid4(),
            document_id=doc.id,
            job_id=job.id,
            field_name="total_amount",
            extracted_value="120.00",
            confidence_score=0.7000,
            is_reviewed=False,
        )
        session.add(header_1)
        session.add(header_2)

        # Create extracted line items
        line_1 = IdpExtractedLineItem(
            id=uuid4(),
            document_id=doc.id,
            job_id=job.id,
            row_index=1,
            column_name="item_name",
            extracted_value="Apples",
            confidence_score=0.9000,
            is_reviewed=False,
        )
        session.add(line_1)
        await session.commit()
        
        yield {
            "user": user,
            "org": org,
            "idp_agent": idp_agent,
            "base_agent": base_agent,
            "doc": doc,
            "job": job,
            "header_1": header_1,
            "header_2": header_2,
            "line_1": line_1,
        }

        # Cleanup test data after test runs
        async with session_scope() as cleanup_session:
            # Delete review sessions
            sessions = (await cleanup_session.exec(select(IdpReviewSession).where(IdpReviewSession.document_id == doc.id))).all()
            for s in sessions:
                await cleanup_session.delete(s)

            # Delete extraction results
            headers = (await cleanup_session.exec(select(IdpExtractedHeader).where(IdpExtractedHeader.document_id == doc.id))).all()
            for h in headers:
                await cleanup_session.delete(h)

            lines = (await cleanup_session.exec(select(IdpExtractedLineItem).where(IdpExtractedLineItem.document_id == doc.id))).all()
            for l in lines:
                await cleanup_session.delete(l)

            # Delete job
            db_job = await cleanup_session.get(IdpProcessingJob, job.id)
            if db_job:
                await cleanup_session.delete(db_job)

            # Delete doc
            db_doc = await cleanup_session.get(IdpDocument, doc.id)
            if db_doc:
                await cleanup_session.delete(db_doc)

            # Delete IDP agent
            db_idp_agent = await cleanup_session.get(IdpAgent, idp_agent.id)
            if db_idp_agent:
                await cleanup_session.delete(db_idp_agent)

            # Delete base agent
            db_base_agent = await cleanup_session.get(Agent, base_agent.id)
            if db_base_agent:
                await cleanup_session.delete(db_base_agent)

            # Delete project
            db_proj = await cleanup_session.get(Project, proj.id)
            if db_proj:
                await cleanup_session.delete(db_proj)

            # Delete membership, org, user
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
async def test_processed_docs_flow(setup_test_data):
    global mock_user
    data = setup_test_data
    user = data["user"]
    doc = data["doc"]
    header_1 = data["header_1"]
    line_1 = data["line_1"]

    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    app.dependency_overrides[idp_rbac] = get_mock_user
    
    client = TestClient(app)
    mock_user = user

    # 1. GET /processed-docs (list docs)
    response = client.get("/api/v1/idp/processed-docs/")
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["total"] >= 1
    assert any(d["id"] == str(doc.id) for d in res_data["items"])

    # Test filters
    response_filt = client.get("/api/v1/idp/processed-docs/?status_filter=pending_review")
    assert response_filt.status_code == 200
    assert len(response_filt.json()["items"]) >= 1

    # 2. GET /processed-docs/{id} (doc detail)
    response_det = client.get(f"/api/v1/idp/processed-docs/{doc.id}")
    assert response_det.status_code == 200
    detail = response_det.json()
    assert detail["original_filename"] == "invoice_receipt.pdf"
    assert len(detail["headers"]) == 2
    assert len(detail["line_items"]) == 1

    # 3. PATCH /processed-docs/{id}/fields (human edit/HITL fields update)
    edit_payload = {
        "headers": [
            {"id": str(header_1.id), "value": "Tesco Corp India"}
        ],
        "line_items": [
            {"id": str(line_1.id), "value": "Apples Premium"}
        ]
    }
    response_edit = client.patch(f"/api/v1/idp/processed-docs/{doc.id}/fields", json=edit_payload)
    assert response_edit.status_code == 200
    edited = response_edit.json()
    
    # Assert header edited
    h_edited = next(h for h in edited["headers"] if h["id"] == str(header_1.id))
    assert h_edited["reviewed_value"] == "Tesco Corp India"
    assert h_edited["is_reviewed"] is True

    # Assert line item edited
    li_edited = next(li for li in edited["line_items"] if li["id"] == str(line_1.id))
    assert li_edited["reviewed_value"] == "Apples Premium"
    assert li_edited["is_reviewed"] is True

    # 4. POST /processed-docs/{id}/review (HITL review complete session)
    review_payload = {
        "notes": "Reviewed manually, items corrected."
    }
    response_rev = client.post(f"/api/v1/idp/processed-docs/{doc.id}/review", json=review_payload)
    assert response_rev.status_code == 200
    rev_result = response_rev.json()
    assert rev_result["status"] == "success"
    assert rev_result["document_status"] == "reviewed"
    assert rev_result["review_session_status"] == "corrections_made"

    # Verify review session was saved in DB
    async with session_scope() as session:
        stmt = select(IdpReviewSession).where(IdpReviewSession.document_id == doc.id)
        session_row = (await session.exec(stmt)).first()
        assert session_row is not None
        assert session_row.notes == "Reviewed manually, items corrected."
        assert session_row.final_status == "corrections_made"

    # 5. POST /processed-docs/{id}/approve (approve endpoint)
    response_app = client.post(f"/api/v1/idp/processed-docs/{doc.id}/approve")
    assert response_app.status_code == 200
    app_result = response_app.json()
    assert app_result["status"] == "success"
    assert app_result["document_status"] == "reviewed"
