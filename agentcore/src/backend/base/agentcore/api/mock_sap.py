"""Mock SAP S/4HANA OData v2 server for development and demo purposes.

Mimics the real SAP OData endpoints that sap_client.py calls, so the full
two-way and three-way matching flow can be tested without a real SAP system.

Connector config to use this mock:
  Base URL : http://<host>:<port>/api/mock/sap
  Auth Mode: api_key
  API Key  : mock-demo-key  (any value accepted)

Three built-in test scenarios (use the PO number when uploading an invoice):
  PO-4500000001  →  Full Match    (invoice matches PO + GR exactly)
  PO-4500000002  →  Partial Match (amounts within 2% tolerance)
  PO-4500000003  →  Mismatch      (significant price variance on line 1)
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter(prefix="/mock/sap", tags=["Mock SAP (Demo)"])

# ─────────────────────────── mock PO data ───────────────────────────────

_MOCK_PO: dict[str, dict[str, Any]] = {
    "4500000001": {
        "PurchaseOrder": "4500000001",
        "Supplier": "ACME001",
        "SupplierName": "Acme Technologies Ltd",
        "CompanyCode": "1000",
        "DocumentCurrency": "INR",
        "NetPaymentAmount": "118000.00",
        "TotalNetAmount": "118000.00",
        "PurchaseOrderDate": "/Date(1735689600000)/",
        "to_PurchaseOrderItem": {
            "results": [
                {
                    "PurchaseOrder": "4500000001",
                    "PurchaseOrderItem": "00010",
                    "Material": "LAPTOP-T14",
                    "PurchaseOrderItemText": "ThinkPad T14 Laptop",
                    "OrderQuantity": "10.000",
                    "BaseUnit": "EA",
                    "NetPriceAmount": "11000.00",
                    "PriceUnit": "1",
                    "DocumentCurrency": "INR",
                    "Plant": "1000",
                },
                {
                    "PurchaseOrder": "4500000001",
                    "PurchaseOrderItem": "00020",
                    "Material": "MOUSE-USB",
                    "PurchaseOrderItemText": "USB Wireless Mouse",
                    "OrderQuantity": "50.000",
                    "BaseUnit": "EA",
                    "NetPriceAmount": "160.00",
                    "PriceUnit": "1",
                    "DocumentCurrency": "INR",
                    "Plant": "1000",
                },
            ]
        },
    },
    "4500000002": {
        "PurchaseOrder": "4500000002",
        "Supplier": "INF002",
        "SupplierName": "Infosys BPO Services Ltd",
        "CompanyCode": "1000",
        "DocumentCurrency": "INR",
        "NetPaymentAmount": "269750.00",
        "TotalNetAmount": "269750.00",
        "PurchaseOrderDate": "/Date(1735689600000)/",
        "to_PurchaseOrderItem": {
            "results": [
                {
                    "PurchaseOrder": "4500000002",
                    "PurchaseOrderItem": "00010",
                    "Material": "SERVER-R740",
                    "PurchaseOrderItemText": "Dell PowerEdge R740 Server",
                    "OrderQuantity": "2.000",
                    "BaseUnit": "EA",
                    "NetPriceAmount": "110000.00",
                    "PriceUnit": "1",
                    "DocumentCurrency": "INR",
                    "Plant": "1000",
                },
                {
                    "PurchaseOrder": "4500000002",
                    "PurchaseOrderItem": "00020",
                    "Material": "STORAGE-SAN",
                    "PurchaseOrderItemText": "SAN Storage 10TB",
                    "OrderQuantity": "5.000",
                    "BaseUnit": "EA",
                    "NetPriceAmount": "9950.00",
                    "PriceUnit": "1",
                    "DocumentCurrency": "INR",
                    "Plant": "1000",
                },
                {
                    "PurchaseOrder": "4500000002",
                    "PurchaseOrderItem": "00030",
                    "Material": "RACK-42U",
                    "PurchaseOrderItemText": "Server Rack 42U",
                    "OrderQuantity": "1.000",
                    "BaseUnit": "EA",
                    "NetPriceAmount": "29750.00",
                    "PriceUnit": "1",
                    "DocumentCurrency": "INR",
                    "Plant": "1000",
                },
            ]
        },
    },
    "4500000003": {
        "PurchaseOrder": "4500000003",
        "Supplier": "TCS003",
        "SupplierName": "Tata Consultancy Services Ltd",
        "CompanyCode": "1000",
        "DocumentCurrency": "INR",
        "NetPaymentAmount": "500000.00",
        "TotalNetAmount": "500000.00",
        "PurchaseOrderDate": "/Date(1735689600000)/",
        "to_PurchaseOrderItem": {
            "results": [
                {
                    "PurchaseOrder": "4500000003",
                    "PurchaseOrderItem": "00010",
                    "Material": "SW-LIC-ERP",
                    "PurchaseOrderItemText": "ERP Software License (Annual)",
                    "OrderQuantity": "10.000",
                    "BaseUnit": "EA",
                    "NetPriceAmount": "45000.00",
                    "PriceUnit": "1",
                    "DocumentCurrency": "INR",
                    "Plant": "1000",
                },
                {
                    "PurchaseOrder": "4500000003",
                    "PurchaseOrderItem": "00020",
                    "Material": "SVC-SUPPORT",
                    "PurchaseOrderItemText": "Annual Support & Maintenance",
                    "OrderQuantity": "1.000",
                    "BaseUnit": "EA",
                    "NetPriceAmount": "50000.00",
                    "PriceUnit": "1",
                    "DocumentCurrency": "INR",
                    "Plant": "1000",
                },
            ]
        },
    },
}

# ─────────────────────────── mock GR data ───────────────────────────────

_MOCK_GR: dict[str, dict[str, Any]] = {
    # PO 4500000001 — full GR (everything received)
    "4500000001": {
        "results": [
            {
                "MaterialDocument": "5000000001",
                "MaterialDocumentYear": "2026",
                "ReferenceDocument": "4500000001",
                "PostingDate": "/Date(1735689600000)/",
                "to_MaterialDocumentItem": {
                    "results": [
                        {"Material": "LAPTOP-T14", "QuantityInBaseUnit": "10.000", "BaseUnit": "EA"},
                        {"Material": "MOUSE-USB",  "QuantityInBaseUnit": "50.000", "BaseUnit": "EA"},
                    ]
                },
            }
        ]
    },
    # PO 4500000002 — partial GR (1 of 2 servers received, storage not yet)
    "4500000002": {
        "results": [
            {
                "MaterialDocument": "5000000002",
                "MaterialDocumentYear": "2026",
                "ReferenceDocument": "4500000002",
                "PostingDate": "/Date(1735689600000)/",
                "to_MaterialDocumentItem": {
                    "results": [
                        {"Material": "SERVER-R740",  "QuantityInBaseUnit": "1.000", "BaseUnit": "EA"},
                        {"Material": "RACK-42U",     "QuantityInBaseUnit": "1.000", "BaseUnit": "EA"},
                    ]
                },
            }
        ]
    },
    # PO 4500000003 — full GR received
    "4500000003": {
        "results": [
            {
                "MaterialDocument": "5000000003",
                "MaterialDocumentYear": "2026",
                "ReferenceDocument": "4500000003",
                "PostingDate": "/Date(1735689600000)/",
                "to_MaterialDocumentItem": {
                    "results": [
                        {"Material": "SW-LIC-ERP",  "QuantityInBaseUnit": "10.000", "BaseUnit": "EA"},
                        {"Material": "SVC-SUPPORT", "QuantityInBaseUnit": "1.000",  "BaseUnit": "EA"},
                    ]
                },
            }
        ]
    },
}

# ─────────────────────────── helpers ────────────────────────────────────

def _po_response(po_number: str) -> JSONResponse:
    normalized = re.sub(r"^[Pp][Oo][-#\s]*", "", po_number.strip()).lstrip("0") or po_number.strip()
    # try exact key first, then zero-padded 10-digit
    data = (
        _MOCK_PO.get(po_number)
        or _MOCK_PO.get(normalized)
        or _MOCK_PO.get(normalized.zfill(10))
    )
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"Purchase Order '{po_number}' not found in mock data. "
                   "Use 4500000001, 4500000002, or 4500000003.",
        )
    return JSONResponse({"d": data})


def _gr_response(po_number: str) -> JSONResponse:
    normalized = re.sub(r"^[Pp][Oo][-#\s]*", "", po_number.strip()).lstrip("0") or po_number.strip()
    data = (
        _MOCK_GR.get(po_number)
        or _MOCK_GR.get(normalized)
        or _MOCK_GR.get(normalized.zfill(10))
        or {"results": []}
    )
    return JSONResponse({"d": data})


_METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
  <edmx:DataServices m:DataServiceVersion="2.0"
    xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
    <Schema Namespace="API_PURCHASEORDER_PROCESS_SRV"
      xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
      <EntityType Name="A_PurchaseOrderType">
        <Key><PropertyRef Name="PurchaseOrder"/></Key>
        <Property Name="PurchaseOrder" Type="Edm.String"/>
        <Property Name="Supplier" Type="Edm.String"/>
        <Property Name="NetPaymentAmount" Type="Edm.Decimal"/>
      </EntityType>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>"""


# ─────────────────────────── routes ─────────────────────────────────────

@router.get("/API_PURCHASEORDER_PROCESS_SRV/{path:path}")
async def po_service(path: str, request: Request) -> Response:
    """Handles $metadata and A_PurchaseOrder('{po}') requests."""
    if path.strip("/") == "$metadata":
        return Response(content=_METADATA_XML, media_type="application/xml")

    m = re.match(r"A_PurchaseOrder\('([^']+)'\)", path)
    if m:
        return _po_response(m.group(1))

    raise HTTPException(status_code=404, detail=f"Mock PO service: unknown path '{path}'")


@router.get("/API_GOODSMOVEMENT_SRV/{path:path}")
async def gr_service(path: str, request: Request) -> JSONResponse:
    """Handles A_MaterialDocumentHeader?$filter=ReferenceDocument eq '{po}' requests."""
    filter_param = request.query_params.get("$filter", "")
    m = re.search(r"ReferenceDocument eq '([^']+)'", filter_param)
    if m:
        return _gr_response(m.group(1))

    raise HTTPException(
        status_code=400,
        detail="Mock GR service: missing or invalid $filter (expected ReferenceDocument eq '{po}')",
    )


# ─────────────────────────── info endpoint ──────────────────────────────

@router.get("/")
async def mock_sap_info() -> dict:
    """Returns available test scenarios for the mock SAP server."""
    return {
        "service": "Mock SAP S/4HANA OData v2",
        "mode": "demo",
        "connector_base_url": "<your-backend-url>/api/mock/sap",
        "auth": "Any API key value accepted",
        "test_scenarios": {
            "4500000001": {
                "vendor": "Acme Technologies Ltd",
                "total_inr": 118000.00,
                "lines": 2,
                "expected_match": "full_match (upload invoice with exact same values)",
            },
            "4500000002": {
                "vendor": "Infosys BPO Services Ltd",
                "total_inr": 269750.00,
                "lines": 3,
                "expected_match": "partial_match (upload invoice with amounts ±1.5%)",
            },
            "4500000003": {
                "vendor": "Tata Consultancy Services Ltd",
                "total_inr": 500000.00,
                "lines": 2,
                "expected_match": "mismatch (upload invoice with line-1 price ~55000 vs PO 45000)",
            },
        },
    }
