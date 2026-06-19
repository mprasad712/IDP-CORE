"""IDP date-range report endpoints — summary correctness (no join fan-out), scope parity
with the Processed Docs list, and the two export shapes (summary + combined all-data).

Seeds one org with an output-wired agent (its docs are reportable) and a second agent
WITHOUT the Processed Docs Output node (its doc must be invisible — proving the filter).
The reportable ``doc1`` deliberately has 2 jobs + 2 review sessions + 2 line-item rows so a
naive one-to-many join would inflate it; the window/count design must yield exactly one row.
"""
import csv
import io
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlmodel import select

from agentcore.main import create_app
from agentcore.services.deps import session_scope
from agentcore.services.auth.utils import get_current_active_user
from agentcore.api.idp import idp_rbac, _idp_review_rbac
from agentcore.services.database.models.user.model import User
from agentcore.services.database.models.organization.model import Organization
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.database.models.idp.config import IdpAgent, IdpReviewSession
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpProcessingJob,
    IdpExtractedHeader,
    IdpExtractedLineItem,
)
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


@pytest.fixture
async def setup_report_data():
    async with session_scope() as session:
        role = (await session.exec(select(Role).where(Role.name == "idp_configurator"))).first()
        sfx = uuid4().hex[:8]

        user = User(id=uuid4(), username=f"rep_{sfx}@t.com", email=f"rep_{sfx}@t.com",
                    password="x", is_active=True, is_superuser=False, role="idp_configurator")
        session.add(user)
        await session.flush()

        org = Organization(id=uuid4(), name=f"Rep Org {sfx}", owner_user_id=user.id, created_by=user.id)
        session.add(org)
        await session.commit()
        await session.refresh(user)
        await session.refresh(org)

        session.add(UserOrganizationMembership(id=uuid4(), user_id=user.id, org_id=org.id, status="active", role_id=role.id))
        proj = Project(id=uuid4(), name="Rep Proj", user_id=user.id)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)

        base_out = Agent(id=uuid4(), name="Rep Agent Out", user_id=user.id, project_id=proj.id, org_id=org.id, data={})
        base_noout = Agent(id=uuid4(), name="Rep Agent NoOut", user_id=user.id, project_id=proj.id, org_id=org.id, data={})
        session.add(base_out); session.add(base_noout)
        await session.commit()
        await session.refresh(base_out); await session.refresh(base_noout)

        agent_out = IdpAgent(id=uuid4(), agent_id=base_out.id, extraction_mode="dynamic_prompting",
                             default_rule_action="pending_review", is_active=True,
                             extra={"has_processed_docs_output": "true"})
        agent_noout = IdpAgent(id=uuid4(), agent_id=base_noout.id, extraction_mode="dynamic_prompting",
                               default_rule_action="pending_review", is_active=True, extra={})
        session.add(agent_out); session.add(agent_noout)
        await session.commit()
        await session.refresh(agent_out); await session.refresh(agent_noout)

        now = datetime.now(timezone.utc)

        # doc1: reportable, reviewed, with 2 jobs + 2 review sessions + 2 header fields + 2 line rows.
        doc1 = IdpDocument(id=uuid4(), agent_id=agent_out.id, original_filename="po_alpha.pdf",
                           file_path="x/po_alpha.pdf", file_type="pdf", file_size_bytes=10, source="upload",
                           status="reviewed", overall_confidence=0.9,
                           processing_started_at=now - timedelta(minutes=2), processing_completed_at=now - timedelta(minutes=1))
        # doc2: reportable, pending_review, 1 header, 1 job, no review.
        doc2 = IdpDocument(id=uuid4(), agent_id=agent_out.id, original_filename="po_beta.pdf",
                           file_path="x/po_beta.pdf", file_type="pdf", file_size_bytes=10, source="upload",
                           status="pending_review", overall_confidence=0.6)
        # doc3: under the NON-output agent → must be invisible in the report AND the list.
        doc3 = IdpDocument(id=uuid4(), agent_id=agent_noout.id, original_filename="po_hidden.pdf",
                           file_path="x/po_hidden.pdf", file_type="pdf", file_size_bytes=10, source="upload",
                           status="reviewed", overall_confidence=0.8)
        for d in (doc1, doc2, doc3):
            session.add(d)
        await session.commit()
        for d in (doc1, doc2, doc3):
            await session.refresh(d)

        job1 = IdpProcessingJob(id=uuid4(), document_id=doc1.id, agent_id=agent_out.id, status="completed",
                                processing_time_ms=5000, created_at=now - timedelta(minutes=10))
        job2 = IdpProcessingJob(id=uuid4(), document_id=doc1.id, agent_id=agent_out.id, status="completed",
                                processing_time_ms=6000, created_at=now)
        job_d2 = IdpProcessingJob(id=uuid4(), document_id=doc2.id, agent_id=agent_out.id, status="completed",
                                  processing_time_ms=3000, created_at=now)
        job_d3 = IdpProcessingJob(id=uuid4(), document_id=doc3.id, agent_id=agent_noout.id, status="completed",
                                  processing_time_ms=4000, created_at=now)
        for j in (job1, job2, job_d2, job_d3):
            session.add(j)
        await session.commit()
        for j in (job1, job2, job_d2, job_d3):
            await session.refresh(j)

        # doc1 fields (linked to the latest job)
        session.add(IdpExtractedHeader(id=uuid4(), document_id=doc1.id, job_id=job2.id, field_name="vendor",
                                       extracted_value="Acme", reviewed_value="Acme Corp", is_reviewed=True, confidence_score=0.9))
        session.add(IdpExtractedHeader(id=uuid4(), document_id=doc1.id, job_id=job2.id, field_name="total",
                                       extracted_value="100", reviewed_value=None, is_reviewed=False, confidence_score=0.8))
        for ri in (1, 2):
            session.add(IdpExtractedLineItem(id=uuid4(), document_id=doc1.id, job_id=job2.id, row_index=ri,
                                             column_name="item", extracted_value=f"W{ri}", is_reviewed=False, confidence_score=0.7))
            session.add(IdpExtractedLineItem(id=uuid4(), document_id=doc1.id, job_id=job2.id, row_index=ri,
                                             column_name="qty", extracted_value=str(ri), is_reviewed=False, confidence_score=0.7))
        # doc2 field
        session.add(IdpExtractedHeader(id=uuid4(), document_id=doc2.id, job_id=job_d2.id, field_name="po_num",
                                       extracted_value="PO-9", reviewed_value=None, is_reviewed=False, confidence_score=0.6))
        # doc3 field (hidden)
        session.add(IdpExtractedHeader(id=uuid4(), document_id=doc3.id, job_id=job_d3.id, field_name="x",
                                       extracted_value="y", is_reviewed=False, confidence_score=0.5))

        rs1 = IdpReviewSession(id=uuid4(), document_id=doc1.id, reviewed_by=user.id,
                               review_started_at=now - timedelta(minutes=9), review_completed_at=now - timedelta(minutes=9),
                               final_status="approved", created_at=now - timedelta(minutes=9))
        rs2 = IdpReviewSession(id=uuid4(), document_id=doc1.id, reviewed_by=user.id,
                               review_started_at=now, review_completed_at=now, final_status="corrections_made", created_at=now)
        session.add(rs1); session.add(rs2)
        await session.commit()

        ids = {"user": user, "org": org, "proj": proj,
               "base_out": base_out, "base_noout": base_noout, "agent_out": agent_out, "agent_noout": agent_noout,
               "doc1": doc1.id, "doc2": doc2.id, "doc3": doc3.id}
        yield ids

        async with session_scope() as cs:
            for did in (doc1.id, doc2.id, doc3.id):
                for m in (await cs.exec(select(IdpReviewSession).where(IdpReviewSession.document_id == did))).all():
                    await cs.delete(m)
                for m in (await cs.exec(select(IdpExtractedHeader).where(IdpExtractedHeader.document_id == did))).all():
                    await cs.delete(m)
                for m in (await cs.exec(select(IdpExtractedLineItem).where(IdpExtractedLineItem.document_id == did))).all():
                    await cs.delete(m)
                for m in (await cs.exec(select(IdpProcessingJob).where(IdpProcessingJob.document_id == did))).all():
                    await cs.delete(m)
                obj = await cs.get(IdpDocument, did)
                if obj: await cs.delete(obj)
            for aid in (agent_out.id, agent_noout.id):
                obj = await cs.get(IdpAgent, aid)
                if obj: await cs.delete(obj)
            for aid in (base_out.id, base_noout.id):
                obj = await cs.get(Agent, aid)
                if obj: await cs.delete(obj)
            obj = await cs.get(Project, proj.id)
            if obj: await cs.delete(obj)
            for m in (await cs.exec(select(UserOrganizationMembership).where(UserOrganizationMembership.user_id == user.id))).all():
                await cs.delete(m)
            obj = await cs.get(Organization, org.id)
            if obj: await cs.delete(obj)
            obj = await cs.get(User, user.id)
            if obj: await cs.delete(obj)
            await cs.commit()


def _client():
    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    app.dependency_overrides[idp_rbac] = get_mock_user
    app.dependency_overrides[_idp_review_rbac] = get_mock_user
    return app, TestClient(app)


@pytest.mark.anyio
async def test_report_summary_no_inflation(setup_report_data):
    global mock_user
    data = setup_report_data
    mock_user = data["user"]
    app, client = _client()

    r = client.get("/api/v1/idp/reports/processed-docs", params={"size": 50})
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {row["document_id"]: row for row in body["items"]}

    # Exactly the 2 output-wired docs; the non-output doc is invisible.
    assert str(data["doc1"]) in rows
    assert str(data["doc2"]) in rows
    assert str(data["doc3"]) not in rows
    assert body["total"] == 2

    d1 = rows[str(data["doc1"])]
    # Appears ONCE despite 2 jobs + 2 review sessions + 4 line-item cells.
    assert sum(1 for it in body["items"] if it["document_id"] == str(data["doc1"])) == 1
    assert d1["header_count"] == 2
    assert d1["line_item_count"] == 2          # 2 logical rows (distinct row_index), not 4 cells
    assert d1["processing_time_ms"] == 6000    # latest job, not the older 5000
    assert d1["review_final_status"] == "corrections_made"  # latest review session
    assert d1["reviewer"] is not None
    assert d1["has_log"] is True
    assert d1["overall_confidence"] == 90.0
    assert d1["pipeline_completed_at"] is not None

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_report_scope_parity_with_list(setup_report_data):
    """A doc is visible in the report iff it's visible in the Processed Docs list (same user)."""
    global mock_user
    data = setup_report_data
    mock_user = data["user"]
    app, client = _client()

    rep = client.get("/api/v1/idp/reports/processed-docs", params={"size": 100}).json()
    lst = client.get("/api/v1/idp/processed-docs/", params={"size": 100}).json()
    rep_ids = {row["document_id"] for row in rep["items"]}
    lst_ids = {row["id"] for row in lst["items"]}
    assert rep_ids == lst_ids

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_report_filters(setup_report_data):
    global mock_user
    data = setup_report_data
    mock_user = data["user"]
    app, client = _client()

    r = client.get("/api/v1/idp/reports/processed-docs", params={"status_filter": "pending_review", "size": 50}).json()
    ids = {row["document_id"] for row in r["items"]}
    assert ids == {str(data["doc2"])}

    r2 = client.get("/api/v1/idp/reports/processed-docs",
                    params={"agent_id": str(data["agent_out"].id), "size": 50}).json()
    assert {str(data["doc1"]), str(data["doc2"])} == {row["document_id"] for row in r2["items"]}

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_report_export_summary_formats(setup_report_data):
    global mock_user
    data = setup_report_data
    mock_user = data["user"]
    app, client = _client()

    for fmt, ext in [("csv", "csv"), ("excel", "xlsx"), ("xml", "xml")]:
        r = client.get("/api/v1/idp/reports/processed-docs/export", params={"format": fmt})
        assert r.status_code == 200, f"{fmt}: {r.text}"
        assert r.content
        assert f".{ext}" in r.headers.get("content-disposition", "")

    # CSV content includes both reportable docs and not the hidden one.
    csv_text = client.get("/api/v1/idp/reports/processed-docs/export", params={"format": "csv"}).content.decode("utf-8-sig")
    reader = list(csv.reader(io.StringIO(csv_text)))
    assert reader[0] == [
        "document_id", "original_filename", "agent_id", "predicted_type", "status",
        "overall_confidence", "uploaded_at", "processing_started_at", "pipeline_completed_at",
        "processing_time_ms", "reviewer", "reviewed_at", "review_final_status",
        "header_count", "line_item_count", "has_log",
    ]
    assert "po_alpha.pdf" in csv_text and "po_beta.pdf" in csv_text
    assert "po_hidden.pdf" not in csv_text

    # bad format → 400
    assert client.get("/api/v1/idp/reports/processed-docs/export", params={"format": "json"}).status_code == 400

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_report_export_data_combined(setup_report_data):
    global mock_user
    data = setup_report_data
    mock_user = data["user"]
    app, client = _client()

    # both (default): predicted/audited/is_reviewed columns present
    both = client.get("/api/v1/idp/reports/processed-docs/export-data", params={"format": "csv"})
    assert both.status_code == 200
    text = both.content.decode("utf-8-sig")
    header = next(csv.reader(io.StringIO(text)))
    assert "predicted_value" in header and "audited_value" in header and "is_reviewed" in header
    assert "Acme Corp" in text   # the reviewed vendor field's audited value
    assert "po_hidden" not in text

    # final: a single 'value' column
    final = client.get("/api/v1/idp/reports/processed-docs/export-data", params={"format": "csv", "values": "final"})
    assert final.status_code == 200
    fheader = next(csv.reader(io.StringIO(final.content.decode("utf-8-sig"))))
    assert "value" in fheader and "predicted_value" not in fheader

    # bad values → 400
    assert client.get("/api/v1/idp/reports/processed-docs/export-data", params={"values": "nope"}).status_code == 400

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_report_export_row_cap(setup_report_data, monkeypatch):
    """Over MAX_EXPORT_ROWS → 422 (don't stream an unbounded file)."""
    global mock_user
    data = setup_report_data
    mock_user = data["user"]
    app, client = _client()

    monkeypatch.setattr("agentcore.api.idp.reports.MAX_EXPORT_ROWS", 1)
    r = client.get("/api/v1/idp/reports/processed-docs/export", params={"format": "csv"})
    assert r.status_code == 422  # 2 reportable docs > cap of 1

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_report_org_isolation(setup_report_data):
    """A user from another (empty) org sees no rows and an empty export."""
    global mock_user
    data = setup_report_data
    from types import SimpleNamespace
    mock_user = SimpleNamespace(id=uuid4(), role="idp_configurator")
    app, client = _client()

    body = client.get("/api/v1/idp/reports/processed-docs", params={"size": 50}).json()
    assert body["total"] == 0
    # export still 200 with just a header row
    exp = client.get("/api/v1/idp/reports/processed-docs/export", params={"format": "csv"})
    assert exp.status_code == 200
    rows = list(csv.reader(io.StringIO(exp.content.decode("utf-8-sig"))))
    assert len(rows) == 1  # header only

    app.dependency_overrides.clear()
