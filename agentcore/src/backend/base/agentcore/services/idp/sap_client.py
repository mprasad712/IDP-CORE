"""SAP S/4HANA OData client for PO and GR lookups.

Supports two auth modes:
  - api_key  : SAP Business Accelerator Hub sandbox (Header: APIKey)
  - oauth2   : Production SAP BTP (client_credentials grant)

Base URL examples:
  Sandbox : https://sandbox.api.sap.com/s4hanacloud/sap/opu/odata/sap
  Production: https://{host}/sap/opu/odata/sap
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TOKEN_EXPIRY_BUFFER = 60  # refresh OAuth token 60 s before expiry


class SapClientError(Exception):
    """Raised when the SAP API returns an error or is unreachable."""


class SapS4HanaClient:
    """Async SAP OData v2 client. Instantiate once per match run."""

    def __init__(self, config: dict[str, Any]) -> None:
        """
        config keys:
          base_url              str  — OData base URL (no trailing slash)
          auth_mode             str  — "api_key" | "oauth2"
          api_key               str  — required when auth_mode == "api_key"
          oauth_token_url       str  — required when auth_mode == "oauth2"
          client_id             str  — required when auth_mode == "oauth2"
          client_secret         str  — required when auth_mode == "oauth2"
        """
        self._base_url = config["base_url"].rstrip("/")
        self._auth_mode: str = config.get("auth_mode", "api_key")
        self._api_key: str = config.get("api_key", "")
        self._oauth_token_url: str = config.get("oauth_token_url", "")
        self._client_id: str = config.get("client_id", "")
        self._client_secret: str = config.get("client_secret", "")

        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # ──────────────────────────── public API ────────────────────────────

    async def fetch_purchase_order(self, po_number: str) -> dict[str, Any]:
        """Return PO header + line items for *po_number*."""
        po_number = _normalize_po_number(po_number)
        url = (
            f"{self._base_url}/API_PURCHASEORDER_PROCESS_SRV"
            f"/A_PurchaseOrder('{po_number}')"
            "?$expand=to_PurchaseOrderItem&$format=json"
        )
        data = await self._get_all_pages(url)
        return data

    async def fetch_goods_receipt(self, po_number: str) -> dict[str, Any]:
        """Return aggregated GR data for all material documents linked to *po_number*."""
        po_number = _normalize_po_number(po_number)
        url = (
            f"{self._base_url}/API_GOODSMOVEMENT_SRV"
            f"/A_MaterialDocumentHeader"
            f"?$filter=ReferenceDocument eq '{po_number}'"
            "&$expand=to_MaterialDocumentItem&$format=json"
        )
        raw = await self._get_all_pages(url)
        return _aggregate_gr_results(raw)

    async def test_connection(self) -> dict[str, Any]:
        """Hit the PO service $metadata to verify connectivity. Returns latency info."""
        url = f"{self._base_url}/API_PURCHASEORDER_PROCESS_SRV/$metadata"
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=10) as client:
            headers = await self._auth_headers()
            resp = client.get(url, headers=headers)
            resp = await resp  # type: ignore[assignment]
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            raise SapClientError(f"SAP metadata request failed: HTTP {resp.status_code}")
        return {"status": "connected", "latency_ms": latency_ms}

    # ─────────────────────────── internals ──────────────────────────────

    async def _get_all_pages(self, url: str) -> dict[str, Any]:
        """Follow OData @odata.nextLink pagination and merge results."""
        async with httpx.AsyncClient(timeout=30) as client:
            headers = await self._auth_headers()
            result: dict[str, Any] = {}
            items: list[Any] = []
            current_url: str | None = url

            while current_url:
                resp = await client.get(current_url, headers=headers)
                if resp.status_code >= 400:
                    raise SapClientError(
                        f"SAP OData request failed: HTTP {resp.status_code} — {resp.text[:300]}"
                    )
                body = resp.json()
                d = body.get("d", body)

                if not result:
                    result = dict(d)

                # Collect paginated results
                results_list = d.get("results", [])
                if isinstance(results_list, list):
                    items.extend(results_list)

                current_url = body.get("d", {}).get("__next") or body.get("@odata.nextLink")

            if items:
                result["results"] = items
            return result

    async def _auth_headers(self) -> dict[str, str]:
        if self._auth_mode == "api_key":
            return {"APIKey": self._api_key, "Accept": "application/json"}

        # oauth2 — refresh if expired
        if not self._access_token or time.time() >= self._token_expires_at:
            await self._refresh_oauth_token()
        return {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}

    async def _refresh_oauth_token(self) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                self._oauth_token_url,
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._client_secret),
            )
            if resp.status_code >= 400:
                raise SapClientError(f"OAuth2 token request failed: HTTP {resp.status_code}")
            body = resp.json()
            self._access_token = body["access_token"]
            expires_in = int(body.get("expires_in", 3600))
            self._token_expires_at = time.time() + expires_in - _TOKEN_EXPIRY_BUFFER


# ─────────────────────────── helpers ────────────────────────────────────

def _normalize_po_number(po_number: str) -> str:
    """Strip common human-readable prefixes (PO-, PO#, etc.) and leading zeros."""
    cleaned = re.sub(r"^[Pp][Oo][-#\s]*", "", po_number.strip())
    # SAP often stores with leading zeros — keep the raw numeric portion as-is
    return cleaned or po_number.strip()


def _aggregate_gr_results(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge multiple GR documents for the same PO into a single quantity-summed view."""
    gr_documents = raw.get("results", [])
    aggregated: dict[str, float] = {}  # material -> total qty received

    for doc in gr_documents:
        items = doc.get("to_MaterialDocumentItem", {})
        if isinstance(items, dict):
            items = items.get("results", [])
        for item in items:
            material = item.get("Material", "")
            qty = _safe_float(item.get("QuantityInBaseUnit", "0"))
            aggregated[material] = aggregated.get(material, 0.0) + qty

    return {
        "documents": gr_documents,
        "aggregated_qty_by_material": aggregated,
    }


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
