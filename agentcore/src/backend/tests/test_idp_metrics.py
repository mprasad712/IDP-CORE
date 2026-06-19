"""IDP dashboard metrics — org-scoped counts, series shape, and tenant isolation.

These exercise the new /api/dashboard/sections/idp-* endpoints. All data is seeded under
a fresh org so a scoped (non-root) member sees EXACTLY the seeded counts, and a user from a
different (empty) org sees zeros — proving the org-scoping holds (no cross-tenant leak).
"""
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlmodel import select

from agentcore.main import create_app
from agentcore.services.deps import session_scope
from agentcore.services.auth.utils import get_current_active_user
from agentcore.services.database.models.user.model import User
from agentcore.services.database.models.organization.model import Organization
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.database.models.idp.config import IdpAgent, IdpReviewSession
from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
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


def _kpi(body, kpi_id):
    return next(k for k in body["kpis"] if k["id"] == kpi_id)


@pytest.fixture
async def setup_metrics_data():
    """Org A with 3 docs (reviewed, pending_review, auto_approved), a timed job + a review session;
    plus an empty Org B with its own user for the isolation check."""
    async with session_scope() as session:
        role = (await session.exec(select(Role).where(Role.name == "idp_configurator"))).first()
        sfx = uuid4().hex[:8]

        user_a = User(id=uuid4(), username=f"mx_a_{sfx}@t.com", email=f"mx_a_{sfx}@t.com",
                      password="x", is_active=True, is_superuser=False, role="idp_configurator")
        user_b = User(id=uuid4(), username=f"mx_b_{sfx}@t.com", email=f"mx_b_{sfx}@t.com",
                      password="x", is_active=True, is_superuser=False, role="idp_configurator")
        session.add(user_a); session.add(user_b)
        await session.flush()

        org_a = Organization(id=uuid4(), name=f"MX Org A {sfx}", owner_user_id=user_a.id, created_by=user_a.id)
        org_b = Organization(id=uuid4(), name=f"MX Org B {sfx}", owner_user_id=user_b.id, created_by=user_b.id)
        session.add(org_a); session.add(org_b)
        await session.commit()
        for u in (user_a, user_b):
            await session.refresh(u)
        await session.refresh(org_a); await session.refresh(org_b)

        session.add(UserOrganizationMembership(id=uuid4(), user_id=user_a.id, org_id=org_a.id, status="active", role_id=role.id))
        session.add(UserOrganizationMembership(id=uuid4(), user_id=user_b.id, org_id=org_b.id, status="active", role_id=role.id))

        proj = Project(id=uuid4(), name="MX Proj", user_id=user_a.id)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)

        base_agent = Agent(id=uuid4(), name="MX Agent", user_id=user_a.id, project_id=proj.id, org_id=org_a.id, data={})
        session.add(base_agent)
        await session.commit()
        await session.refresh(base_agent)

        idp_agent = IdpAgent(id=uuid4(), agent_id=base_agent.id, extraction_mode="dynamic_prompting",
                             default_rule_action="pending_review", is_active=True,
                             extra={"has_processed_docs_output": "true"})
        session.add(idp_agent)
        await session.commit()
        await session.refresh(idp_agent)

        def _doc(status, uploaded_by=None):
            return IdpDocument(id=uuid4(), agent_id=idp_agent.id, original_filename=f"{status}.pdf",
                               file_path=f"x/{status}.pdf", file_type="pdf", file_size_bytes=10,
                               source="upload", status=status, overall_confidence=0.9, uploaded_by=uploaded_by)

        doc_rev = _doc("reviewed", user_a.id)
        doc_pend = _doc("pending_review", user_a.id)
        doc_auto = _doc("auto_approved")  # not uploaded by user_a
        doc_fail = _doc("failed")  # a hard pipeline failure (not uploaded by user_a)
        for d in (doc_rev, doc_pend, doc_auto, doc_fail):
            session.add(d)
        await session.commit()
        for d in (doc_rev, doc_pend, doc_auto, doc_fail):
            await session.refresh(d)

        # doc_rev was re-processed: an OLD completed job (5000ms) and a LATER one (6000ms).
        # avg_processing_seconds must use only the latest per document → 6.0s (not the 5.5 mean).
        now = datetime.now(timezone.utc)
        job = IdpProcessingJob(id=uuid4(), document_id=doc_rev.id, agent_id=idp_agent.id,
                               status="completed", processing_time_ms=5000,
                               created_at=now - timedelta(minutes=10))
        job2 = IdpProcessingJob(id=uuid4(), document_id=doc_rev.id, agent_id=idp_agent.id,
                                status="completed", processing_time_ms=6000, created_at=now)
        session.add(job)
        session.add(job2)
        rs = IdpReviewSession(id=uuid4(), document_id=doc_rev.id, reviewed_by=user_a.id,
                              review_started_at=doc_rev.created_at, review_completed_at=doc_rev.updated_at,
                              final_status="approved")
        session.add(rs)
        await session.commit()

        ids = {
            "user_a": user_a, "user_b": user_b, "org_a": org_a, "org_b": org_b,
            "idp_agent": idp_agent, "base_agent": base_agent, "proj": proj,
            "docs": [doc_rev.id, doc_pend.id, doc_auto.id, doc_fail.id],
            "jobs": [job.id, job2.id], "rs": rs.id,
        }
        yield ids

        async with session_scope() as cs:
            for rid in (rs.id,):
                obj = await cs.get(IdpReviewSession, rid)
                if obj: await cs.delete(obj)
            for jid in (job.id, job2.id):
                obj = await cs.get(IdpProcessingJob, jid)
                if obj: await cs.delete(obj)
            for did in ids["docs"]:
                obj = await cs.get(IdpDocument, did)
                if obj: await cs.delete(obj)
            obj = await cs.get(IdpAgent, idp_agent.id)
            if obj: await cs.delete(obj)
            obj = await cs.get(Agent, base_agent.id)
            if obj: await cs.delete(obj)
            obj = await cs.get(Project, proj.id)
            if obj: await cs.delete(obj)
            for uid in (user_a.id, user_b.id):
                for m in (await cs.exec(select(UserOrganizationMembership).where(UserOrganizationMembership.user_id == uid))).all():
                    await cs.delete(m)
            for oid in (org_a.id, org_b.id):
                obj = await cs.get(Organization, oid)
                if obj: await cs.delete(obj)
            for uid in (user_a.id, user_b.id):
                obj = await cs.get(User, uid)
                if obj: await cs.delete(obj)
            await cs.commit()


@pytest.mark.anyio
async def test_idp_pipeline_section_scoped_counts(setup_metrics_data):
    global mock_user
    data = setup_metrics_data
    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    client = TestClient(app)

    # Scoped member of Org A sees EXACTLY the 4 seeded docs (reviewed, pending, auto, failed).
    mock_user = data["user_a"]
    r = client.get("/api/dashboard/sections/idp-pipeline")
    assert r.status_code == 200, r.text
    body = r.json()
    assert _kpi(body, "docs_processed")["value"] == 4
    assert _kpi(body, "docs_failed")["value"] == 1  # explicit failure breakdown KPI
    assert _kpi(body, "pending_review")["value"] == 1
    # Success = processed without a HARD failure: (4 - 1 failed) / 4 = 75%. pending_review counts as success.
    assert _kpi(body, "success_rate")["value"] == 75.0
    # doc_rev has TWO completed jobs (5000ms old, 6000ms latest) → latest-per-doc avg = 6.0s, not 5.5.
    assert _kpi(body, "avg_processing_time")["value"] == 6.0

    # throughput series: shape correct + total equals the 4 seeded docs (all created today).
    rs = client.get("/api/dashboard/sections/idp-pipeline/throughput-series", params={"range": "30d"})
    assert rs.status_code == 200, rs.text
    sbody = rs.json()
    assert len(sbody["series"]) == 30
    assert sum(p["value"] for p in sbody["series"]) == 4

    # my-submissions: only the 2 docs uploaded_by user_a (reviewed + pending).
    rm = client.get("/api/dashboard/sections/idp-my-submissions")
    assert rm.status_code == 200, rm.text
    mbody = rm.json()
    assert _kpi(mbody, "total_submitted")["value"] == 2
    assert _kpi(mbody, "under_review")["value"] == 1
    assert _kpi(mbody, "approved")["value"] == 1
    assert _kpi(mbody, "failed_skipped")["value"] == 0  # the failed doc is not user_a's

    # field-quality: accuracy = avg overall_confidence (0.9 → 90%); failed = 1 (hard fail only).
    rq = client.get("/api/dashboard/sections/idp-field-quality")
    assert rq.status_code == 200, rq.text
    qbody = rq.json()
    assert _kpi(qbody, "avg_extraction_accuracy")["value"] == 90.0
    assert _kpi(qbody, "docs_processed")["value"] == 4
    assert _kpi(qbody, "failed_extractions")["value"] == 1

    # analytics: success/error rates are complementary over the 4 docs (1 hard failure).
    ra = client.get("/api/dashboard/sections/idp-analytics")
    assert ra.status_code == 200, ra.text
    abody = ra.json()
    assert _kpi(abody, "total_docs_processed")["value"] == 4
    assert _kpi(abody, "success_rate")["value"] == 75.0
    assert _kpi(abody, "processing_error_rate")["value"] == 25.0

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_idp_metrics_org_isolation(setup_metrics_data):
    """A user from a different (empty) org must NOT see Org A's documents."""
    global mock_user
    data = setup_metrics_data
    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    client = TestClient(app)

    mock_user = data["user_b"]  # member of empty Org B
    body = client.get("/api/dashboard/sections/idp-pipeline").json()
    assert _kpi(body, "docs_processed")["value"] == 0
    assert _kpi(body, "pending_review")["value"] == 0
    assert _kpi(body, "active_field_configs")["value"] == 0  # global templates NOT leaked to a no-data org

    # field-quality + analytics queries must also be org-scoped (no cross-tenant leak).
    qbody = client.get("/api/dashboard/sections/idp-field-quality").json()
    assert _kpi(qbody, "avg_extraction_accuracy")["value"] == 0
    assert _kpi(qbody, "failed_extractions")["value"] == 0
    abody = client.get("/api/dashboard/sections/idp-analytics").json()
    assert _kpi(abody, "total_docs_processed")["value"] == 0
    assert _kpi(abody, "success_rate")["value"] == 0

    # review-queue (count_review_sessions path) scoped too.
    rvbody = client.get("/api/dashboard/sections/idp-review-queue").json()
    assert _kpi(rvbody, "reviewed_week")["value"] == 0

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_all_idp_sections_return_200(setup_metrics_data):
    """Every IDP dashboard section + series returns 200 in the expected shape."""
    global mock_user
    data = setup_metrics_data
    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    client = TestClient(app)
    mock_user = data["user_a"]

    sections = [
        "idp-pipeline", "idp-review-queue", "idp-approval-queue",
        "idp-my-submissions", "idp-field-quality", "idp-analytics",
    ]
    for s in sections:
        r = client.get(f"/api/dashboard/sections/{s}")
        assert r.status_code == 200, f"{s}: {r.text}"
        assert isinstance(r.json()["kpis"], list) and len(r.json()["kpis"]) >= 1

    series = [
        ("idp-pipeline/throughput-series", "30d"),
        ("idp-review-queue/activity-series", "7d"),
        ("idp-approval-queue/activity-series", "7d"),
        ("idp-my-submissions/activity-series", "7d"),
        ("idp-field-quality/volume-series", "30d"),
        ("idp-analytics/throughput-series", "30d"),
        ("idp-analytics/cost-series", "30d"),
    ]
    for path, rng in series:
        r = client.get(f"/api/dashboard/sections/{path}", params={"range": rng})
        assert r.status_code == 200, f"{path}: {r.text}"
        assert isinstance(r.json()["series"], list)

    app.dependency_overrides.clear()
