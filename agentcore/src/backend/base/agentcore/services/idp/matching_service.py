"""Two-way and three-way invoice matching against SAP PO/GR data.

Two-Way Match  : Invoice ↔ PO  (vendor, amounts, line quantities/prices)
Three-Way Match: Invoice ↔ PO ↔ GR  (adds goods receipt quantity verification)

Results are persisted to idp_match_results + idp_match_discrepancies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from agentcore.services.database.models.idp.match import (
    IdpAgentMatchConfig,
    IdpMatchDiscrepancy,
    IdpMatchResult,
)
from agentcore.services.idp.sap_client import SapS4HanaClient, _safe_float

logger = logging.getLogger(__name__)

# ─────────────────────────── data classes ───────────────────────────────


@dataclass
class MatchConfig:
    match_type: str  # "2way" | "3way"
    amount_tolerance_pct: float = 2.0
    quantity_tolerance_pct: float = 0.0
    unit_price_tolerance_pct: float = 2.0
    vendor_name_fuzzy_threshold: float = 0.85
    sap_connector_id: str | None = None


@dataclass
class DiscrepancyRow:
    scope: str  # "header" | "line_item"
    field_name: str
    invoice_value: str | None
    po_value: str | None
    gr_value: str | None
    variance_pct: float | None
    status: str  # "full_match" | "partial_match" | "mismatch"
    line_item_index: int | None = None


@dataclass
class MatchRunResult:
    overall_status: str  # "full_match" | "partial_match" | "mismatch" | "error"
    match_score: float
    po_number: str
    gr_document_number: str | None
    discrepancies: list[DiscrepancyRow] = field(default_factory=list)
    error_message: str | None = None


# ─────────────────────────── matching service ───────────────────────────


class MatchingService:
    def __init__(self, config: MatchConfig, sap_client: SapS4HanaClient) -> None:
        self._config = config
        self._sap = sap_client

    async def run(
        self,
        headers: dict[str, str],
        line_items: list[dict[str, str]],
    ) -> MatchRunResult:
        """Entry point — delegates to 2-way or 3-way based on config."""
        try:
            if self._config.match_type == "3way":
                return await self._three_way(headers, line_items)
            return await self._two_way(headers, line_items)
        except Exception as exc:
            logger.exception("SAP matching failed: %s", exc)
            return MatchRunResult(
                overall_status="error",
                match_score=0.0,
                po_number=headers.get("po_number", ""),
                gr_document_number=None,
                error_message=str(exc),
            )

    # ─────────────────────────── two-way ────────────────────────────────

    async def _two_way(
        self,
        headers: dict[str, str],
        line_items: list[dict[str, str]],
    ) -> MatchRunResult:
        po_number = headers.get("po_number", "").strip()
        po_data = await self._sap.fetch_purchase_order(po_number)
        discs = self._compare_invoice_po(headers, line_items, po_data)
        status, score = _compute_overall(discs)
        return MatchRunResult(
            overall_status=status,
            match_score=score,
            po_number=po_number,
            gr_document_number=None,
            discrepancies=discs,
        )

    # ─────────────────────────── three-way ──────────────────────────────

    async def _three_way(
        self,
        headers: dict[str, str],
        line_items: list[dict[str, str]],
    ) -> MatchRunResult:
        po_number = headers.get("po_number", "").strip()
        po_data, gr_data = await _gather(
            self._sap.fetch_purchase_order(po_number),
            self._sap.fetch_goods_receipt(po_number),
        )
        discs = self._compare_invoice_po(headers, line_items, po_data)
        gr_discs = self._compare_gr(line_items, po_data, gr_data)
        discs.extend(gr_discs)
        status, score = _compute_overall(discs)
        gr_doc_number = _first_gr_doc_number(gr_data)
        return MatchRunResult(
            overall_status=status,
            match_score=score,
            po_number=po_number,
            gr_document_number=gr_doc_number,
            discrepancies=discs,
        )

    # ───────────────────── comparison logic: PO ─────────────────────────

    def _compare_invoice_po(
        self,
        headers: dict[str, str],
        line_items: list[dict[str, str]],
        po_data: dict[str, Any],
    ) -> list[DiscrepancyRow]:
        rows: list[DiscrepancyRow] = []

        # ── Vendor name ──
        inv_vendor = headers.get("supplier_name", "")
        sap_vendor = po_data.get("SupplierName", "") or po_data.get("Supplier", "")
        if inv_vendor or sap_vendor:
            ratio = SequenceMatcher(None, inv_vendor.lower(), sap_vendor.lower()).ratio()
            if ratio >= self._config.vendor_name_fuzzy_threshold:
                vstatus = "full_match" if ratio == 1.0 else "partial_match"
                vpct = round((1 - ratio) * 100, 2)
            else:
                vstatus = "mismatch"
                vpct = round((1 - ratio) * 100, 2)
            rows.append(DiscrepancyRow(
                scope="header", field_name="vendor_name",
                invoice_value=inv_vendor, po_value=sap_vendor, gr_value=None,
                variance_pct=vpct, status=vstatus,
            ))

        # ── Invoice total ──
        inv_total = _safe_float(headers.get("invoice_value") or headers.get("invoice_taxable_value"))
        sap_total = _safe_float(po_data.get("NetPaymentAmount") or po_data.get("TotalNetAmount"))
        rows.append(self._amount_row("header", "total_amount", inv_total, sap_total, self._config.amount_tolerance_pct))

        # ── Line items ──
        po_items: list[dict[str, Any]] = po_data.get("to_PurchaseOrderItem", {}).get("results", [])
        for idx, inv_item in enumerate(line_items):
            po_item = _match_line_item(inv_item, po_items, idx)
            if po_item is None:
                rows.append(DiscrepancyRow(
                    scope="line_item", field_name=f"line[{idx}].po_match",
                    invoice_value=inv_item.get("description", ""), po_value=None, gr_value=None,
                    variance_pct=None, status="mismatch", line_item_index=idx,
                ))
                continue

            inv_qty = _safe_float(inv_item.get("quantity"))
            sap_qty = _safe_float(po_item.get("OrderQuantity"))
            rows.append(self._amount_row(
                "line_item", f"line[{idx}].quantity",
                inv_qty, sap_qty, self._config.quantity_tolerance_pct, idx,
            ))

            inv_price = _safe_float(inv_item.get("unit_price"))
            sap_price = _safe_float(po_item.get("NetPriceAmount"))
            rows.append(self._amount_row(
                "line_item", f"line[{idx}].unit_price",
                inv_price, sap_price, self._config.unit_price_tolerance_pct, idx,
            ))

        return rows

    # ───────────────────── comparison logic: GR ─────────────────────────

    def _compare_gr(
        self,
        line_items: list[dict[str, str]],
        po_data: dict[str, Any],
        gr_data: dict[str, Any],
    ) -> list[DiscrepancyRow]:
        rows: list[DiscrepancyRow] = []
        agg_qty: dict[str, float] = gr_data.get("aggregated_qty_by_material", {})
        po_items: list[dict[str, Any]] = po_data.get("to_PurchaseOrderItem", {}).get("results", [])

        for idx, inv_item in enumerate(line_items):
            po_item = _match_line_item(inv_item, po_items, idx)
            if po_item is None:
                continue
            material = po_item.get("Material", "")
            inv_qty = _safe_float(inv_item.get("quantity"))
            gr_qty = agg_qty.get(material, 0.0)
            rows.append(self._amount_row(
                "line_item", f"line[{idx}].gr_quantity",
                inv_qty, gr_qty, self._config.quantity_tolerance_pct, idx,
                gr_value=str(gr_qty) if gr_qty else None,
            ))
        return rows

    # ────────────────────────── helpers ─────────────────────────────────

    def _amount_row(
        self,
        scope: str,
        field_name: str,
        inv_val: float,
        sap_val: float,
        tolerance_pct: float,
        line_idx: int | None = None,
        *,
        gr_value: str | None = None,
    ) -> DiscrepancyRow:
        vpct = _pct_variance(inv_val, sap_val)
        status = _field_status(vpct, tolerance_pct)
        return DiscrepancyRow(
            scope=scope,
            field_name=field_name,
            invoice_value=str(inv_val) if inv_val is not None else None,
            po_value=str(sap_val) if sap_val is not None else None,
            gr_value=gr_value,
            variance_pct=round(vpct, 4),
            status=status,
            line_item_index=line_idx,
        )


# ─────────────────────────── DB persistence ─────────────────────────────


async def persist_match_result(
    session: AsyncSession,
    document_id: UUID,
    result: MatchRunResult,
    config: MatchConfig,
    sap_po_snapshot: dict[str, Any] | None,
    sap_gr_snapshot: dict[str, Any] | None,
) -> IdpMatchResult:
    """Write IdpMatchResult + IdpMatchDiscrepancy rows and return the result row."""
    now = datetime.now(timezone.utc)
    match_row = IdpMatchResult(
        id=uuid4(),
        document_id=document_id,
        match_type=config.match_type,
        overall_status=result.overall_status,
        match_score=result.match_score,
        po_number=result.po_number,
        gr_document_number=result.gr_document_number,
        sap_po_snapshot=sap_po_snapshot,
        sap_gr_snapshot=sap_gr_snapshot,
        sap_connector_id=UUID(config.sap_connector_id) if config.sap_connector_id else None,
        tolerance_config={
            "match_type": config.match_type,
            "amount_tolerance_pct": config.amount_tolerance_pct,
            "quantity_tolerance_pct": config.quantity_tolerance_pct,
            "unit_price_tolerance_pct": config.unit_price_tolerance_pct,
            "vendor_name_fuzzy_threshold": config.vendor_name_fuzzy_threshold,
        },
        created_at=now,
        updated_at=now,
    )
    session.add(match_row)
    await session.flush()  # get match_row.id

    for disc in result.discrepancies:
        session.add(IdpMatchDiscrepancy(
            id=uuid4(),
            match_result_id=match_row.id,
            document_id=document_id,
            scope=disc.scope,
            line_item_index=disc.line_item_index,
            field_name=disc.field_name,
            invoice_value=disc.invoice_value,
            po_value=disc.po_value,
            gr_value=disc.gr_value,
            variance_pct=disc.variance_pct,
            status=disc.status,
            created_at=now,
        ))

    await session.commit()
    return match_row


async def load_agent_match_config(
    session: AsyncSession,
    agent_id: UUID,
) -> IdpAgentMatchConfig | None:
    result = await session.execute(
        select(IdpAgentMatchConfig).where(IdpAgentMatchConfig.agent_id == agent_id)
    )
    return result.scalar_one_or_none()


# ───────────────────────── pure helpers ─────────────────────────────────


def _pct_variance(inv: float, sap: float) -> float:
    if sap == 0:
        return 100.0 if inv != 0 else 0.0
    return abs((inv - sap) / sap) * 100


def _field_status(variance_pct: float, tolerance_pct: float) -> str:
    if variance_pct == 0.0:
        return "full_match"
    if variance_pct <= tolerance_pct:
        return "partial_match"
    return "mismatch"


def _compute_overall(discs: list[DiscrepancyRow]) -> tuple[str, float]:
    if not discs:
        return "full_match", 100.0
    total = len(discs)
    matched = sum(1 for d in discs if d.status == "full_match")
    partial = sum(1 for d in discs if d.status == "partial_match")
    score = round(((matched + partial * 0.5) / total) * 100, 1)
    if any(d.status == "mismatch" for d in discs):
        return "mismatch", score
    if any(d.status == "partial_match" for d in discs):
        return "partial_match", score
    return "full_match", 100.0


def _match_line_item(
    inv_item: dict[str, str],
    po_items: list[dict[str, Any]],
    fallback_idx: int,
) -> dict[str, Any] | None:
    """Match invoice line item to PO item by HSN code, then positional fallback."""
    hsn = inv_item.get("hsn_code", "").strip()
    if hsn:
        for po in po_items:
            if po.get("ProductType", "") == hsn or po.get("Plant", "") == hsn:
                return po
    if fallback_idx < len(po_items):
        return po_items[fallback_idx]
    return None


def _first_gr_doc_number(gr_data: dict[str, Any]) -> str | None:
    docs = gr_data.get("documents", [])
    if docs:
        return str(docs[0].get("MaterialDocument", "")) or None
    return None


async def _gather(*coros):  # type: ignore[no-untyped-def]
    """Run coroutines concurrently (asyncio.gather wrapper to avoid import at module level)."""
    import asyncio
    return await asyncio.gather(*coros)
