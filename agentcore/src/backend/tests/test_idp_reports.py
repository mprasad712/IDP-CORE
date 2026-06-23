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
from agentcore.services.database.models.idp.config import (
    IdpAgent,
    IdpFieldConfigHeader,
    IdpFieldConfigLineItem,
    IdpFieldConfiguration,
    IdpReviewSession,
)
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpDocumentClassification,
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
async def test_report_export_data_sectioned(setup_report_data):
    """All-data export = sectioned per-file blocks (file-info + HEADER FIELDS table beside a
    LINE ITEMS table), stacked. doc1 = 2 line rows + reviewed vendor; doc2 = header-only; doc3 hidden.
    Line items are audit-aware just like headers."""
    global mock_user
    data = setup_report_data
    mock_user = data["user"]
    app, client = _client()

    # both (default): each header + line field shows Predicted / Audited / Reviewed / Confidence
    both = client.get("/api/v1/idp/reports/processed-docs/export-data", params={"format": "csv"})
    assert both.status_code == 200
    text = both.content.decode("utf-8-sig")
    cells = [c for row in csv.reader(io.StringIO(text)) for c in row]
    # per-file sections + side-by-side table labels
    assert "FILE  po_alpha.pdf" in cells and "FILE  po_beta.pdf" in cells
    assert "FILE  po_hidden.pdf" not in cells          # hidden doc excluded
    assert "HEADER FIELDS" in cells and "LINE ITEMS" in cells
    # header table sub-headers + line-item audit columns (line items audited like headers)
    assert "Field" in cells and "Predicted" in cells and "Audited" in cells and "Confidence" in cells
    assert "item" in cells and "item (audited)" in cells and "qty (conf)" in cells
    # line items now carry a "(reviewed)" sub-column too (parity with header fields)
    assert "item (reviewed)" in cells and "qty (reviewed)" in cells
    # vendor predicted + audited both present
    assert "Acme" in text and "Acme Corp" in text
    # file-info block carries metadata + reviewer (doc1 has a review session)
    assert "Reviewed by" in cells and "Confidence" in cells

    # final: single value per field/cell, no audited/conf variants
    final = client.get(
        "/api/v1/idp/reports/processed-docs/export-data", params={"format": "csv", "values": "final"}
    )
    assert final.status_code == 200
    ftext = final.content.decode("utf-8-sig")
    fcells = [c for row in csv.reader(io.StringIO(ftext)) for c in row]
    assert "Field" in fcells and "Value" in fcells
    assert "Predicted" not in fcells and "item (audited)" not in fcells
    assert "Acme Corp" in ftext        # vendor final value = the reviewed value

    # predicted (no-config): single value, LLM output only → vendor shows "Acme", not "Acme Corp"
    pre = client.get("/api/v1/idp/reports/processed-docs/export-data", params={"format": "csv", "values": "predicted"})
    assert pre.status_code == 200
    ptext = pre.content.decode("utf-8-sig")
    assert "Acme" in ptext and "Acme Corp" not in ptext
    assert "Predicted" not in [c for row in csv.reader(io.StringIO(ptext)) for c in row]  # one value column

    # audited (no-config): human value only; vendor reviewed → "Acme Corp"; total un-reviewed → not "100"
    aud = client.get("/api/v1/idp/reports/processed-docs/export-data", params={"format": "csv", "values": "audited"})
    assert aud.status_code == 200
    atext = aud.content.decode("utf-8-sig")
    assert "Acme Corp" in atext and "Acme" not in atext.replace("Acme Corp", "")  # only the reviewed form

    # excel + xml still produce valid attachments
    xl = client.get("/api/v1/idp/reports/processed-docs/export-data", params={"format": "excel"})
    assert xl.status_code == 200 and xl.content[:2] == b"PK"
    assert client.get("/api/v1/idp/reports/processed-docs/export-data", params={"format": "xml"}).status_code == 200

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


# ──────────────────────────────────────────────────────────────────────
# Config-driven "Download all data" layout (export-data?config_id=…)
# ──────────────────────────────────────────────────────────────────────

_META = ["File", "Document ID", "Type", "Status", "Confidence",
         "Uploaded", "Processed", "Reviewed By", "Reviewed At", "Review"]


def _csv_rows(resp):
    return list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))


def _export(client, **params):
    return client.get("/api/v1/idp/reports/processed-docs/export-data", params=params)


@pytest.fixture
async def setup_config_export():
    """Seed an org with an 'Invoice' config (headers vendor/total, line cols item/qty) and docs
    linked to it three different ways, plus excluded docs, a collision config, a foreign-org config,
    and a global template — to exercise every branch of the config-driven export."""
    async with session_scope() as s:
        role = (await s.exec(select(Role).where(Role.name == "idp_configurator"))).first()
        sfx = uuid4().hex[:8]
        user = User(id=uuid4(), username=f"cex_{sfx}@t.com", email=f"cex_{sfx}@t.com",
                    password="x", is_active=True, is_superuser=False, role="idp_configurator")
        s.add(user); await s.flush()
        org = Organization(id=uuid4(), name=f"CEX Org {sfx}", owner_user_id=user.id, created_by=user.id)
        forg = Organization(id=uuid4(), name=f"CEX Foreign {sfx}", owner_user_id=user.id, created_by=user.id)
        s.add(org); s.add(forg); await s.commit(); await s.refresh(user); await s.refresh(org); await s.refresh(forg)
        s.add(UserOrganizationMembership(id=uuid4(), user_id=user.id, org_id=org.id, status="active", role_id=role.id))
        proj = Project(id=uuid4(), name="CEX Proj", user_id=user.id)
        s.add(proj); await s.commit(); await s.refresh(proj)

        def _cfg(name, org_id, is_template=False):
            return IdpFieldConfiguration(id=uuid4(), name=f"{name} {sfx}", org_id=org_id,
                                         is_template=is_template, is_active=True, doc_type=name, visibility="org")
        config_inv = _cfg("Invoice", org.id)
        config_other = _cfg("Other", org.id)
        config_collide = _cfg("Collide", org.id)
        config_foreign = _cfg("Foreign", forg.id)               # user is NOT a member → 404
        template_cfg = _cfg("GlobalTmpl", None, is_template=True)  # global template → accessible
        for c in (config_inv, config_other, config_collide, config_foreign, template_cfg):
            s.add(c)
        await s.commit()
        for c in (config_inv, config_other, config_collide, config_foreign, template_cfg):
            await s.refresh(c)

        # Invoice schema: headers vendor(0), total(1); line cols item(0), qty(1).
        s.add(IdpFieldConfigHeader(id=uuid4(), config_id=config_inv.id, field_name="vendor", field_type="text", display_order=0))
        s.add(IdpFieldConfigHeader(id=uuid4(), config_id=config_inv.id, field_name="total", field_type="number", display_order=1))
        s.add(IdpFieldConfigLineItem(id=uuid4(), config_id=config_inv.id, column_name="item", column_type="text", display_order=0))
        s.add(IdpFieldConfigLineItem(id=uuid4(), config_id=config_inv.id, column_name="qty", column_type="number", display_order=1))
        # Collide schema: a header AND a line column both named "qty".
        s.add(IdpFieldConfigHeader(id=uuid4(), config_id=config_collide.id, field_name="qty", field_type="text", display_order=0))
        s.add(IdpFieldConfigLineItem(id=uuid4(), config_id=config_collide.id, column_name="qty", column_type="number", display_order=0))
        await s.commit()

        def _agent(base_name, field_config_id, output=True):
            base = Agent(id=uuid4(), name=base_name, user_id=user.id, project_id=proj.id, org_id=org.id, data={})
            s.add(base)
            return base
        base_inv = _agent("CEX Inv", config_inv.id)
        base_cls = _agent("CEX Cls", config_other.id)
        base_excl = _agent("CEX Excl", config_other.id)
        base_coll = _agent("CEX Coll", config_collide.id)
        await s.commit()
        for b in (base_inv, base_cls, base_excl, base_coll):
            await s.refresh(b)

        def _idp_agent(base, fcid):
            return IdpAgent(id=uuid4(), agent_id=base.id, extraction_mode="named_config", field_config_id=fcid,
                            default_rule_action="pending_review", is_active=True, extra={"has_processed_docs_output": "true"})
        agent_inv = _idp_agent(base_inv, config_inv.id)      # named-config → doc_a, doc_b included
        agent_cls = _idp_agent(base_cls, config_other.id)    # named-config = Other; doc_c included ONLY via classification
        agent_excl = _idp_agent(base_excl, config_other.id)  # named-config = Other; doc_d excluded from Invoice
        agent_coll = _idp_agent(base_coll, config_collide.id)
        for a in (agent_inv, agent_cls, agent_excl, agent_coll):
            s.add(a)
        await s.commit()
        for a in (agent_inv, agent_cls, agent_excl, agent_coll):
            await s.refresh(a)

        now = datetime.now(timezone.utc)

        def _doc(agent, name, status="pending_review", conf=0.8):
            return IdpDocument(id=uuid4(), agent_id=agent.id, original_filename=name, file_path=f"x/{name}",
                               file_type="pdf", file_size_bytes=10, source="upload", status=status, overall_confidence=conf,
                               processing_completed_at=now)
        doc_a = _doc(agent_inv, "inv_a.pdf", status="reviewed", conf=0.9)  # 2 line rows, vendor reviewed, total un-reviewed
        doc_b = _doc(agent_inv, "inv_b.pdf")                              # 0 line items, vendor only (missing total)
        doc_c = _doc(agent_cls, "inv_c.pdf")                             # included via classification only
        doc_d = _doc(agent_excl, "inv_d.pdf")                            # excluded (Other config, no classification)
        doc_e = _doc(agent_coll, "inv_e.pdf")                            # collision (qty header + qty line)
        doc_f = _doc(agent_inv, "inv_f.pdf")     # agent=Invoice BUT classifier picked Other → EXCLUDED from Invoice
        doc_g = _doc(agent_inv, "inv_g.pdf")     # agent=Invoice, no classification, ZERO extracted rows → 1 blank row
        all_docs = (doc_a, doc_b, doc_c, doc_d, doc_e, doc_f, doc_g)
        for d in all_docs:
            s.add(d)
        await s.commit()
        for d in all_docs:
            await s.refresh(d)

        job = {}
        for d in (doc_a, doc_b, doc_c, doc_d, doc_e, doc_f):  # doc_g intentionally has no job / no extraction
            j = IdpProcessingJob(id=uuid4(), document_id=d.id, agent_id=d.agent_id, status="completed",
                                 processing_time_ms=1000, created_at=now)
            s.add(j); job[d.id] = j
        await s.commit()
        for d in (doc_a, doc_b, doc_c, doc_d, doc_e, doc_f):
            await s.refresh(job[d.id])

        def _h(doc, name, ev, rv=None, reviewed=False, conf=0.8):
            s.add(IdpExtractedHeader(id=uuid4(), document_id=doc.id, job_id=job[doc.id].id, field_name=name,
                                     extracted_value=ev, reviewed_value=rv, is_reviewed=reviewed, confidence_score=conf))

        def _li(doc, ri, name, ev, rv=None, reviewed=False, conf=0.7):
            s.add(IdpExtractedLineItem(id=uuid4(), document_id=doc.id, job_id=job[doc.id].id, row_index=ri,
                                       column_name=name, extracted_value=ev, reviewed_value=rv, is_reviewed=reviewed, confidence_score=conf))

        # doc_a: vendor reviewed (Acme→Acme Corp), total un-reviewed (100); 2 line rows.
        _h(doc_a, "vendor", "Acme", "Acme Corp", reviewed=True, conf=0.9)
        _h(doc_a, "total", "100", None, reviewed=False, conf=0.8)
        for ri in (1, 2):
            _li(doc_a, ri, "item", f"W{ri}")
            _li(doc_a, ri, "qty", str(ri))
        # doc_b: vendor only (total MISSING → blank); 0 line items.
        _h(doc_b, "vendor", "Beta", None, reviewed=False)
        # doc_c: vendor; 1 line row.
        _h(doc_c, "vendor", "Gamma", None, reviewed=False)
        _li(doc_c, 1, "item", "X1")
        _li(doc_c, 1, "qty", "5")
        # doc_d: vendor (must be excluded).
        _h(doc_d, "vendor", "Delta", None, reviewed=False)
        # doc_e: header qty="H" + line qty="L" (collision).
        _h(doc_e, "qty", "H", None, reviewed=False)
        _li(doc_e, 1, "qty", "L")
        # doc_f: a real header, but the classifier overrides agent=Invoice with Other (see below).
        _h(doc_f, "vendor", "Foxtrot", None, reviewed=False)
        # doc_g: NO extracted rows at all.
        await s.commit()

        # doc_a review session; doc_c classifier-selected Invoice; doc_f classifier-selected Other.
        s.add(IdpReviewSession(id=uuid4(), document_id=doc_a.id, reviewed_by=user.id,
                               review_completed_at=now, final_status="approved", created_at=now))
        s.add(IdpDocumentClassification(id=uuid4(), document_id=doc_c.id, predicted_type="Invoice",
                                        confidence=0.95, selected_config_id=config_inv.id, is_selected=True))
        s.add(IdpDocumentClassification(id=uuid4(), document_id=doc_f.id, predicted_type="Other",
                                        confidence=0.90, selected_config_id=config_other.id, is_selected=True))
        await s.commit()

        ids = {"user": user, "org": org,
               "config_inv": config_inv.id, "config_other": config_other.id, "config_collide": config_collide.id,
               "config_foreign": config_foreign.id, "template_cfg": template_cfg.id,
               "doc_a": doc_a.id, "doc_b": doc_b.id, "doc_c": doc_c.id, "doc_d": doc_d.id, "doc_e": doc_e.id,
               "doc_f": doc_f.id, "doc_g": doc_g.id,
               "_docs": [d.id for d in all_docs],
               "_agents": [a.id for a in (agent_inv, agent_cls, agent_excl, agent_coll)],
               "_bases": [b.id for b in (base_inv, base_cls, base_excl, base_coll)],
               "_configs": [config_inv.id, config_other.id, config_collide.id, config_foreign.id, template_cfg.id],
               "proj": proj.id, "forg": forg.id}
        yield ids

        async with session_scope() as cs:
            for did in ids["_docs"]:
                for M in (IdpReviewSession, IdpExtractedHeader, IdpExtractedLineItem, IdpProcessingJob, IdpDocumentClassification):
                    for m in (await cs.exec(select(M).where(M.document_id == did))).all():
                        await cs.delete(m)
                obj = await cs.get(IdpDocument, did)
                if obj: await cs.delete(obj)
            for aid in ids["_agents"]:
                obj = await cs.get(IdpAgent, aid)
                if obj: await cs.delete(obj)
            for cid in ids["_configs"]:
                for M in (IdpFieldConfigHeader, IdpFieldConfigLineItem):
                    for m in (await cs.exec(select(M).where(M.config_id == cid))).all():
                        await cs.delete(m)
                obj = await cs.get(IdpFieldConfiguration, cid)
                if obj: await cs.delete(obj)
            for bid in ids["_bases"]:
                obj = await cs.get(Agent, bid)
                if obj: await cs.delete(obj)
            obj = await cs.get(Project, ids["proj"])
            if obj: await cs.delete(obj)
            for m in (await cs.exec(select(UserOrganizationMembership).where(UserOrganizationMembership.user_id == ids["user"].id))).all():
                await cs.delete(m)
            for oid in (ids["org"].id, ids["forg"]):
                obj = await cs.get(Organization, oid)
                if obj: await cs.delete(obj)
            obj = await cs.get(User, ids["user"].id)
            if obj: await cs.delete(obj)
            await cs.commit()


@pytest.mark.anyio
async def test_config_export_columns_and_value_modes(setup_config_export):
    """Fixed config columns; the 4 value modes apply the right per-field rules + sub-column labels."""
    global mock_user
    data = setup_config_export
    mock_user = data["user"]
    app, client = _client()
    cid = str(data["config_inv"])

    # final / predicted / audited → one column per field, named exactly.
    for mode in ("final", "predicted", "audited"):
        rows = _csv_rows(_export(client, format="csv", config_id=cid, values=mode))
        assert rows[0] == _META + ["vendor", "total", "item", "qty"], f"{mode} columns"

    # both → 4 sub-columns per field with the right suffixes.
    both = _csv_rows(_export(client, format="csv", config_id=cid, values="both"))
    expected = list(_META)
    for f in ("vendor", "total", "item", "qty"):
        expected += [f, f"{f} (audited)", f"{f} (reviewed)", f"{f} (conf)"]
    assert both[0] == expected

    # value-mode cell rules on doc_a row 1 (vendor reviewed Acme→Acme Corp; total un-reviewed 100).
    def row_for(rows, fname):
        fi = rows[0].index("File")
        return next(r for r in rows[1:] if r[fi] == fname)

    fin = _csv_rows(_export(client, format="csv", config_id=cid, values="final"))
    r = row_for(fin, "inv_a.pdf"); hd = fin[0]
    assert r[hd.index("vendor")] == "Acme Corp"   # final = reviewed value
    assert r[hd.index("total")] == "100"          # un-reviewed → predicted

    pre = _csv_rows(_export(client, format="csv", config_id=cid, values="predicted"))
    r = row_for(pre, "inv_a.pdf"); hd = pre[0]
    assert r[hd.index("vendor")] == "Acme"        # predicted = extracted

    aud = _csv_rows(_export(client, format="csv", config_id=cid, values="audited"))
    r = row_for(aud, "inv_a.pdf"); hd = aud[0]
    assert r[hd.index("vendor")] == "Acme Corp"   # reviewed → human value
    assert r[hd.index("total")] == ""             # un-reviewed → BLANK in audited mode

    b = row_for(both, "inv_a.pdf"); hb = both[0]
    assert b[hb.index("vendor")] == "Acme" and b[hb.index("vendor (audited)")] == "Acme Corp"
    assert b[hb.index("vendor (reviewed)")] == "yes" and b[hb.index("vendor (conf)")] == "90.0"
    assert b[hb.index("total (audited)")] == "" and b[hb.index("total (reviewed)")] == "no"

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_config_export_row_repetition_and_zero_lines(setup_config_export):
    """doc_a (2 line items) → 2 rows with meta+headers repeated; doc_b (0 line items) → 1 row."""
    global mock_user
    data = setup_config_export
    mock_user = data["user"]
    app, client = _client()
    rows = _csv_rows(_export(client, format="csv", config_id=str(data["config_inv"]), values="final"))
    hd = rows[0]
    fi, vi, ii, qi = hd.index("File"), hd.index("vendor"), hd.index("item"), hd.index("qty")
    a_rows = [r for r in rows[1:] if r[fi] == "inv_a.pdf"]
    b_rows = [r for r in rows[1:] if r[fi] == "inv_b.pdf"]
    assert len(a_rows) == 2                              # one row per line item
    assert {r[vi] for r in a_rows} == {"Acme Corp"}      # header value repeated on every line row
    assert sorted(r[ii] for r in a_rows) == ["W1", "W2"]
    assert sorted(r[qi] for r in a_rows) == ["1", "2"]
    assert len(b_rows) == 1                              # 0 line items → single row
    assert b_rows[0][vi] == "Beta"
    assert b_rows[0][ii] == "" and b_rows[0][qi] == ""   # no line items → blank line cells
    # doc_b is missing the 'total' config field → blank
    assert b_rows[0][hd.index("total")] == ""
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_config_export_doc_filter_union(setup_config_export):
    """The config actually used governs: classifier selection is authoritative; the agent's named
    config applies only when there is NO classifier selection."""
    global mock_user
    data = setup_config_export
    mock_user = data["user"]
    app, client = _client()
    rows = _csv_rows(_export(client, format="csv", config_id=str(data["config_inv"]), values="final"))
    fi = rows[0].index("File")
    files = {r[fi] for r in rows[1:]}
    assert "inv_a.pdf" in files and "inv_b.pdf" in files  # agent named-config Invoice, no classification
    assert "inv_c.pdf" in files            # classifier-selected Invoice (agent's config = Other)
    assert "inv_g.pdf" in files            # agent=Invoice, no classification, zero extracted rows → still appears
    assert "inv_d.pdf" not in files        # Other config, no Invoice classification → excluded
    assert "inv_f.pdf" not in files        # agent=Invoice BUT classifier picked Other → excluded (classifier wins)

    # the override doc appears under the Other config, not Invoice
    other = _csv_rows(_export(client, format="csv", config_id=str(data["config_other"]), values="final"))
    ofiles = {r[other[0].index("File")] for r in other[1:]}
    assert "inv_f.pdf" in ofiles           # classifier-selected Other
    assert "inv_a.pdf" not in ofiles       # Invoice-linked, not Other
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_config_export_zero_extraction_doc(setup_config_export):
    """A linked doc with zero extracted headers/line items still appears as one blank-field row."""
    global mock_user
    data = setup_config_export
    mock_user = data["user"]
    app, client = _client()
    rows = _csv_rows(_export(client, format="csv", config_id=str(data["config_inv"]), values="final"))
    hd = rows[0]
    g_rows = [r for r in rows[1:] if r[hd.index("File")] == "inv_g.pdf"]
    assert len(g_rows) == 1                              # one row even with no extracted fields
    for col in ("vendor", "total", "item", "qty"):
        assert g_rows[0][hd.index(col)] == ""            # all field columns blank
    assert g_rows[0][hd.index("Document ID")] == str(data["doc_g"])  # meta still present
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_config_export_name_collision(setup_config_export):
    """A header and a line column both named 'qty' stay distinct positional columns with own values."""
    global mock_user
    data = setup_config_export
    mock_user = data["user"]
    app, client = _client()
    rows = _csv_rows(_export(client, format="csv", config_id=str(data["config_collide"]), values="final"))
    hd = rows[0]
    assert hd.count("qty") == 2                       # two distinct positional 'qty' columns
    data_row = next(r for r in rows[1:] if r[hd.index("File")] == "inv_e.pdf")
    # the two 'qty' columns carry the header value and the line value respectively
    qty_positions = [i for i, c in enumerate(hd) if c == "qty"]
    assert [data_row[i] for i in qty_positions] == ["H", "L"]
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_config_export_formats_and_no_match(setup_config_export):
    """csv/excel/xml all valid; a config no doc links to → header-only file."""
    global mock_user
    data = setup_config_export
    mock_user = data["user"]
    app, client = _client()
    cid = str(data["config_inv"])
    assert _export(client, format="csv", config_id=cid).status_code == 200
    xl = _export(client, format="excel", config_id=cid)
    assert xl.status_code == 200 and xl.content[:2] == b"PK"
    assert _export(client, format="xml", config_id=cid).status_code == 200
    # 'Other' config: no doc is named-config-linked to it AND has output... doc_c/doc_d agents use Other
    # but those docs ARE under Other-config agents → Other export returns doc_c & doc_d. Use the template
    # (global, accessible) which no doc links to → header-only.
    tmpl = _csv_rows(_export(client, format="csv", config_id=str(data["template_cfg"]), values="final"))
    assert len(tmpl) == 1                               # only the column header row
    assert tmpl[0][:len(_META)] == _META
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_config_export_access_and_bad_inputs(setup_config_export):
    """Foreign-org config → 404; unknown id → 404; global template → 200; bad values/format → 400."""
    global mock_user
    data = setup_config_export
    mock_user = data["user"]
    app, client = _client()
    assert _export(client, format="csv", config_id=str(data["config_foreign"])).status_code == 404
    assert _export(client, format="csv", config_id=str(uuid4())).status_code == 404
    assert _export(client, format="csv", config_id=str(data["template_cfg"])).status_code == 200
    assert _export(client, format="csv", config_id=str(data["config_inv"]), values="nope").status_code == 400
    assert _export(client, format="json", config_id=str(data["config_inv"])).status_code == 400
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_config_export_row_cap(setup_config_export, monkeypatch):
    """Capped on BOTH extracted cells and linked-doc count (zero-extraction docs are bounded too).
    An accessible config with no linked docs stays a 200 header-only file (no false 422)."""
    global mock_user
    data = setup_config_export
    mock_user = data["user"]
    app, client = _client()
    monkeypatch.setattr("agentcore.api.idp.reports.MAX_EXPORT_ROWS", 1)
    # Invoice export has several cells + several linked docs → over the cap → 422
    assert _export(client, format="csv", config_id=str(data["config_inv"])).status_code == 422
    # A config with zero linked docs (and zero cells) must NOT false-trip the cap.
    empty = _export(client, format="csv", config_id=str(data["template_cfg"]))
    assert empty.status_code == 200 and len(_csv_rows(empty)) == 1  # header only
    app.dependency_overrides.clear()
