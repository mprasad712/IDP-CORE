"""IDP SAP Matching API — trigger, retrieve results, accept/reject discrepancies, manage config."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.database.models.idp.documents import IdpDocument, IdpExtractedHeader, IdpExtractedLineItem
from agentcore.services.database.models.idp.match import (
    IdpAgentMatchConfig,
    IdpMatchDiscrepancy,
    IdpMatchResult,
)

router = APIRouter(tags=["IDP Matching"])


# ─────────────────────── Schemas ─────────────────────────────────────────


class TriggerMatchPayload(BaseModel):
    match_type: str = "2way"  # "2way" | "3way"
    sap_connector_id: UUID
    amount_tolerance_pct: float = 2.0
    quantity_tolerance_pct: float = 0.0
    unit_price_tolerance_pct: float = 2.0
    vendor_name_fuzzy_threshold: float = 0.85


class AcceptDiscrepancyPayload(BaseModel):
    reviewer_note: str | None = None


class MatchConfigPayload(BaseModel):
    match_enabled: bool = False
    match_type: str = "2way"
    amount_tolerance_pct: float = 2.0
    quantity_tolerance_pct: float = 0.0
    unit_price_tolerance_pct: float = 2.0
    vendor_name_fuzzy_threshold: float = 0.85
    sap_connector_id: UUID | None = None


# ─────────────────────── Helpers ─────────────────────────────────────────


def _serialize_discrepancy(d: IdpMatchDiscrepancy) -> dict:
    return {
        "id": str(d.id),
        "scope": d.scope,
        "line_item_index": d.line_item_index,
        "field_name": d.field_name,
        "invoice_value": d.invoice_value,
        "po_value": d.po_value,
        "gr_value": d.gr_value,
        "variance_pct": d.variance_pct,
        "status": d.status,
        "is_accepted": d.is_accepted,
        "reviewer_note": d.reviewer_note,
    }


def _serialize_result(r: IdpMatchResult, discs: list[IdpMatchDiscrepancy]) -> dict:
    return {
        "id": str(r.id),
        "document_id": str(r.document_id),
        "match_type": r.match_type,
        "overall_status": r.overall_status,
        "match_score": r.match_score,
        "po_number": r.po_number,
        "gr_document_number": r.gr_document_number,
        "tolerance_config": r.tolerance_config,
        "reviewed_by": str(r.reviewed_by) if r.reviewed_by else None,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "created_at": r.created_at.isoformat(),
        "discrepancies": [_serialize_discrepancy(d) for d in discs],
    }


# ─────────────────────── Endpoints ───────────────────────────────────────


@router.post("/matching/{document_id}/trigger")
async def trigger_match(
    document_id: UUID,
    payload: TriggerMatchPayload,
    background_tasks: BackgroundTasks,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """Manually trigger a SAP match for a document. Runs in background; poll /results for outcome."""
    doc = await session.get(IdpDocument, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    background_tasks.add_task(
        _run_match_background,
        document_id=document_id,
        payload=payload,
        triggered_by=current_user.id,
    )
    return {"status": "triggered", "document_id": str(document_id), "match_type": payload.match_type}


async def _run_match_background(document_id: UUID, payload: TriggerMatchPayload, triggered_by: UUID) -> None:
    """Background task: run the matching service and persist results."""
    from agentcore.services.database.service import get_session_ctx
    from agentcore.services.idp.matching_service import MatchConfig, MatchingService, persist_match_result
    from agentcore.services.idp.sap_client import SapS4HanaClient
    from agentcore.api.connector_catalogue import _decrypt_provider_config
    from agentcore.services.database.models.connector_catalogue.model import ConnectorCatalogue

    async with get_session_ctx() as session:
        # Load extracted data
        headers_rows = (
            await session.exec(select(IdpExtractedHeader).where(IdpExtractedHeader.document_id == document_id))
        ).all()
        line_item_rows = (
            await session.exec(select(IdpExtractedLineItem).where(IdpExtractedLineItem.document_id == document_id))
        ).all()

        headers_dict = {h.field_name: h.reviewed_value or h.extracted_value for h in headers_rows}
        # Group line items by row_index
        line_items_by_idx: dict[int, dict] = {}
        for li in line_item_rows:
            idx = li.row_index
            if idx not in line_items_by_idx:
                line_items_by_idx[idx] = {}
            line_items_by_idx[idx][li.column_name] = li.reviewed_value or li.extracted_value
        line_items_list = [line_items_by_idx[i] for i in sorted(line_items_by_idx)]

        # Load SAP connector
        connector = await session.get(ConnectorCatalogue, payload.sap_connector_id)
        if not connector:
            return
        sap_config = _decrypt_provider_config(connector.provider, connector.provider_config or {})

        match_config = MatchConfig(
            match_type=payload.match_type,
            amount_tolerance_pct=payload.amount_tolerance_pct,
            quantity_tolerance_pct=payload.quantity_tolerance_pct,
            unit_price_tolerance_pct=payload.unit_price_tolerance_pct,
            vendor_name_fuzzy_threshold=payload.vendor_name_fuzzy_threshold,
            sap_connector_id=str(payload.sap_connector_id),
        )

        sap_client = SapS4HanaClient(sap_config)
        service = MatchingService(match_config, sap_client)
        result = await service.run(headers_dict, line_items_list)

        await persist_match_result(
            session=session,
            document_id=document_id,
            result=result,
            config=match_config,
            sap_po_snapshot=None,
            sap_gr_snapshot=None,
        )


@router.get("/matching/{document_id}/results")
async def get_match_results(
    document_id: UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """Return the latest match result (and all discrepancies) for a document."""
    doc = await session.get(IdpDocument, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    result = (
        await session.exec(
            select(IdpMatchResult)
            .where(IdpMatchResult.document_id == document_id)
            .order_by(IdpMatchResult.created_at.desc())
        )
    ).first()

    if not result:
        return {"has_match": False, "document_id": str(document_id)}

    discs = (
        await session.exec(
            select(IdpMatchDiscrepancy).where(IdpMatchDiscrepancy.match_result_id == result.id)
        )
    ).all()

    return {"has_match": True, **_serialize_result(result, discs)}


@router.patch("/matching/{document_id}/discrepancies/{discrepancy_id}/accept")
async def accept_discrepancy(
    document_id: UUID,
    discrepancy_id: UUID,
    payload: AcceptDiscrepancyPayload,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """Mark a discrepancy as accepted by the reviewer."""
    disc = await session.get(IdpMatchDiscrepancy, discrepancy_id)
    if not disc or disc.document_id != document_id:
        raise HTTPException(status_code=404, detail="Discrepancy not found")

    now = datetime.now(timezone.utc)
    disc.is_accepted = True
    disc.accepted_by = current_user.id
    disc.accepted_at = now
    disc.reviewer_note = payload.reviewer_note
    session.add(disc)

    # Update overall match result reviewed_by/reviewed_at
    match_result = await session.get(IdpMatchResult, disc.match_result_id)
    if match_result:
        match_result.reviewed_by = current_user.id
        match_result.reviewed_at = now
        match_result.updated_at = now
        session.add(match_result)

    await session.commit()
    return _serialize_discrepancy(disc)


@router.patch("/matching/{document_id}/discrepancies/{discrepancy_id}/reject")
async def reject_discrepancy(
    document_id: UUID,
    discrepancy_id: UUID,
    payload: AcceptDiscrepancyPayload,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """Mark a previously accepted discrepancy as rejected (un-accept)."""
    disc = await session.get(IdpMatchDiscrepancy, discrepancy_id)
    if not disc or disc.document_id != document_id:
        raise HTTPException(status_code=404, detail="Discrepancy not found")

    now = datetime.now(timezone.utc)
    disc.is_accepted = False
    disc.accepted_by = current_user.id
    disc.accepted_at = now
    disc.reviewer_note = payload.reviewer_note
    session.add(disc)
    await session.commit()
    return _serialize_discrepancy(disc)


@router.get("/matching/config/{agent_id}")
async def get_match_config(
    agent_id: UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """Get SAP matching configuration for an IDP agent."""
    cfg = (
        await session.exec(
            select(IdpAgentMatchConfig).where(IdpAgentMatchConfig.agent_id == agent_id)
        )
    ).first()
    if not cfg:
        return {
            "agent_id": str(agent_id),
            "match_enabled": False,
            "match_type": "2way",
            "amount_tolerance_pct": 2.0,
            "quantity_tolerance_pct": 0.0,
            "unit_price_tolerance_pct": 2.0,
            "vendor_name_fuzzy_threshold": 0.85,
            "sap_connector_id": None,
        }
    return {
        "agent_id": str(cfg.agent_id),
        "match_enabled": cfg.match_enabled,
        "match_type": cfg.match_type,
        "amount_tolerance_pct": cfg.amount_tolerance_pct,
        "quantity_tolerance_pct": cfg.quantity_tolerance_pct,
        "unit_price_tolerance_pct": cfg.unit_price_tolerance_pct,
        "vendor_name_fuzzy_threshold": cfg.vendor_name_fuzzy_threshold,
        "sap_connector_id": str(cfg.sap_connector_id) if cfg.sap_connector_id else None,
    }


@router.put("/matching/config/{agent_id}")
async def save_match_config(
    agent_id: UUID,
    payload: MatchConfigPayload,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """Create or update SAP matching configuration for an IDP agent."""
    now = datetime.now(timezone.utc)
    cfg = (
        await session.exec(
            select(IdpAgentMatchConfig).where(IdpAgentMatchConfig.agent_id == agent_id)
        )
    ).first()

    if cfg:
        cfg.match_enabled = payload.match_enabled
        cfg.match_type = payload.match_type
        cfg.amount_tolerance_pct = payload.amount_tolerance_pct
        cfg.quantity_tolerance_pct = payload.quantity_tolerance_pct
        cfg.unit_price_tolerance_pct = payload.unit_price_tolerance_pct
        cfg.vendor_name_fuzzy_threshold = payload.vendor_name_fuzzy_threshold
        cfg.sap_connector_id = payload.sap_connector_id
        cfg.updated_at = now
    else:
        from uuid import uuid4
        cfg = IdpAgentMatchConfig(
            id=uuid4(),
            agent_id=agent_id,
            match_enabled=payload.match_enabled,
            match_type=payload.match_type,
            amount_tolerance_pct=payload.amount_tolerance_pct,
            quantity_tolerance_pct=payload.quantity_tolerance_pct,
            unit_price_tolerance_pct=payload.unit_price_tolerance_pct,
            vendor_name_fuzzy_threshold=payload.vendor_name_fuzzy_threshold,
            sap_connector_id=payload.sap_connector_id,
            created_at=now,
            updated_at=now,
        )

    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)

    return {
        "agent_id": str(cfg.agent_id),
        "match_enabled": cfg.match_enabled,
        "match_type": cfg.match_type,
        "amount_tolerance_pct": cfg.amount_tolerance_pct,
        "quantity_tolerance_pct": cfg.quantity_tolerance_pct,
        "unit_price_tolerance_pct": cfg.unit_price_tolerance_pct,
        "vendor_name_fuzzy_threshold": cfg.vendor_name_fuzzy_threshold,
        "sap_connector_id": str(cfg.sap_connector_id) if cfg.sap_connector_id else None,
    }
