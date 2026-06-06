"""Seed global IDP field configuration templates.

Seeds 12 global field configuration templates (org_id=NULL, is_template=TRUE)
that mirror the TEMPLATE_CATALOGUE defined in the frontend.
Idempotent: each template is inserted only if no global template with that name exists.

Revision ID: idp_b04_seed_templates
Revises: idp_b03perm01
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "idp_b04_seed_templates"
down_revision: Union[str, Sequence[str], None] = "idp_b03perm01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (field_name, field_type, is_required, display_order)
_Header = tuple[str, str, bool, int]
# (column_name, column_type, is_required, display_order)
_LineItem = tuple[str, str, bool, int]

TEMPLATES: list[dict] = [
    {
        "name": "Invoice",
        "description": "Standard invoice with vendor, date, line items and totals.",
        "headers": [
            ("invoice_number",  "text",    True,  1),
            ("invoice_date",    "date",    True,  2),
            ("vendor_name",     "text",    True,  3),
            ("vendor_address",  "text",    False, 4),
            ("buyer_name",      "text",    False, 5),
            ("due_date",        "date",    False, 6),
            ("subtotal",        "number",  True,  7),
            ("tax_amount",      "number",  False, 8),
            ("total_amount",    "number",  True,  9),
            ("currency",        "text",    False, 10),
        ],
        "line_items": [
            ("description",  "text",   True,  1),
            ("quantity",     "number", True,  2),
            ("unit_price",   "number", True,  3),
            ("tax_rate",     "number", False, 4),
            ("line_total",   "number", True,  5),
        ],
    },
    {
        "name": "PAN Card",
        "description": "Indian PAN card with name, DOB and PAN number.",
        "headers": [
            ("pan_number",        "text",    True,  1),
            ("full_name",         "text",    True,  2),
            ("father_name",       "text",    False, 3),
            ("date_of_birth",     "date",    True,  4),
            ("signature_present", "boolean", False, 5),
        ],
        "line_items": [],
    },
    {
        "name": "Aadhaar Card",
        "description": "Indian Aadhaar card with UID, name and address.",
        "headers": [
            ("aadhaar_number", "text",  True,  1),
            ("full_name",      "text",  True,  2),
            ("date_of_birth",  "date",  True,  3),
            ("gender",         "text",  False, 4),
            ("address",        "text",  True,  5),
            ("pincode",        "text",  False, 6),
            ("qr_data",        "text",  False, 7),
        ],
        "line_items": [],
    },
    {
        "name": "Purchase Order",
        "description": "PO with buyer, supplier, items and delivery terms.",
        "headers": [
            ("po_number",      "text",   True,  1),
            ("po_date",        "date",   True,  2),
            ("buyer_name",     "text",   True,  3),
            ("supplier_name",  "text",   True,  4),
            ("delivery_date",  "date",   False, 5),
            ("delivery_terms", "text",   False, 6),
            ("total_amount",   "number", True,  7),
            ("currency",       "text",   False, 8),
        ],
        "line_items": [
            ("item_code",   "text",   False, 1),
            ("description", "text",   True,  2),
            ("quantity",    "number", True,  3),
            ("unit",        "text",   False, 4),
            ("unit_price",  "number", True,  5),
            ("line_total",  "number", True,  6),
        ],
    },
    {
        "name": "Receipt",
        "description": "Point-of-sale or payment receipt.",
        "headers": [
            ("merchant_name",    "text",   True,  1),
            ("merchant_address", "text",   False, 2),
            ("transaction_date", "date",   True,  3),
            ("transaction_time", "text",   False, 4),
            ("total_amount",     "number", True,  5),
            ("payment_method",   "text",   False, 6),
            ("receipt_number",   "text",   False, 7),
        ],
        "line_items": [
            ("item",     "text",   True,  1),
            ("quantity", "number", False, 2),
            ("price",    "number", True,  3),
        ],
    },
    {
        "name": "Contract",
        "description": "Legal contract with parties, dates and clauses.",
        "headers": [
            ("contract_title",    "text",    True,  1),
            ("party_one",         "text",    True,  2),
            ("party_two",         "text",    True,  3),
            ("effective_date",    "date",    True,  4),
            ("expiry_date",       "date",    False, 5),
            ("governing_law",     "text",    False, 6),
            ("contract_value",    "number",  False, 7),
            ("signature_present", "boolean", False, 8),
        ],
        "line_items": [],
    },
    {
        "name": "Bank Statement",
        "description": "Bank account transactions for a given period.",
        "headers": [
            ("bank_name",       "text",   True,  1),
            ("account_number",  "text",   True,  2),
            ("account_holder",  "text",   True,  3),
            ("statement_from",  "date",   True,  4),
            ("statement_to",    "date",   True,  5),
            ("opening_balance", "number", False, 6),
            ("closing_balance", "number", True,  7),
            ("currency",        "text",   False, 8),
        ],
        "line_items": [
            ("date",        "date",   True,  1),
            ("description", "text",   True,  2),
            ("debit",       "number", False, 3),
            ("credit",      "number", False, 4),
            ("balance",     "number", True,  5),
        ],
    },
    {
        "name": "Pay Slip",
        "description": "Employee salary slip with earnings and deductions.",
        "headers": [
            ("employee_name",    "text",   True,  1),
            ("employee_id",      "text",   False, 2),
            ("designation",      "text",   False, 3),
            ("pay_period",       "text",   True,  4),
            ("basic_salary",     "number", True,  5),
            ("gross_salary",     "number", True,  6),
            ("total_deductions", "number", False, 7),
            ("net_salary",       "number", True,  8),
        ],
        "line_items": [
            ("component", "text",   True,  1),
            ("type",      "text",   False, 2),
            ("amount",    "number", True,  3),
        ],
    },
    {
        "name": "Passport",
        "description": "Passport with MRZ, name, nationality and expiry.",
        "headers": [
            ("passport_number", "text", True,  1),
            ("surname",         "text", True,  2),
            ("given_names",     "text", True,  3),
            ("nationality",     "text", True,  4),
            ("date_of_birth",   "date", True,  5),
            ("gender",          "text", False, 6),
            ("issue_date",      "date", True,  7),
            ("expiry_date",     "date", True,  8),
            ("mrz_line1",       "text", False, 9),
            ("mrz_line2",       "text", False, 10),
        ],
        "line_items": [],
    },
    {
        "name": "Driving Licence",
        "description": "Driving licence with number, class and expiry.",
        "headers": [
            ("licence_number",    "text", True,  1),
            ("full_name",         "text", True,  2),
            ("date_of_birth",     "date", True,  3),
            ("address",           "text", False, 4),
            ("issue_date",        "date", True,  5),
            ("expiry_date",       "date", True,  6),
            ("vehicle_class",     "text", False, 7),
            ("issuing_authority", "text", False, 8),
        ],
        "line_items": [],
    },
    {
        "name": "Utility Bill",
        "description": "Electricity / water / gas bill with usage and amount.",
        "headers": [
            ("provider_name",  "text",   True,  1),
            ("customer_name",  "text",   True,  2),
            ("account_number", "text",   False, 3),
            ("bill_date",      "date",   True,  4),
            ("due_date",       "date",   False, 5),
            ("billing_period", "text",   False, 6),
            ("units_consumed", "number", False, 7),
            ("total_amount",   "number", True,  8),
        ],
        "line_items": [],
    },
    {
        "name": "Medical Report",
        "description": "Lab / diagnostic report with patient and findings.",
        "headers": [
            ("patient_name",     "text", True,  1),
            ("patient_id",       "text", False, 2),
            ("date_of_birth",    "date", False, 3),
            ("report_date",      "date", True,  4),
            ("report_type",      "text", True,  5),
            ("referring_doctor", "text", False, 6),
            ("lab_name",         "text", False, 7),
        ],
        "line_items": [
            ("test_name", "text", True,  1),
            ("result",    "text", True,  2),
            ("reference", "text", False, 3),
            ("unit",      "text", False, 4),
            ("status",    "text", False, 5),
        ],
    },
]

_CHECK_TEMPLATE = sa.text(
    "SELECT id FROM idp_field_configurations "
    "WHERE name = :name AND org_id IS NULL AND is_template = TRUE "
    "LIMIT 1"
)

_INSERT_CONFIG = sa.text(
    "INSERT INTO idp_field_configurations "
    "(id, name, description, org_id, is_template, is_active, created_at, updated_at) "
    "VALUES (gen_random_uuid(), :name, :description, NULL, TRUE, TRUE, now(), now()) "
    "RETURNING id"
)

_INSERT_HEADER = sa.text(
    "INSERT INTO idp_field_config_headers "
    "(id, config_id, field_name, field_type, is_required, display_order, created_at, updated_at) "
    "VALUES (gen_random_uuid(), :config_id, :field_name, :field_type, :is_required, :display_order, now(), now())"
)

_INSERT_LINE_ITEM = sa.text(
    "INSERT INTO idp_field_config_line_items "
    "(id, config_id, column_name, column_type, is_required, display_order, created_at, updated_at) "
    "VALUES (gen_random_uuid(), :config_id, :column_name, :column_type, :is_required, :display_order, now(), now())"
)


def upgrade() -> None:
    conn = op.get_bind()

    for tpl in TEMPLATES:
        existing = conn.execute(_CHECK_TEMPLATE, {"name": tpl["name"]}).fetchone()
        if existing:
            continue

        row = conn.execute(_INSERT_CONFIG, {"name": tpl["name"], "description": tpl["description"]}).fetchone()
        config_id = row[0]

        for field_name, field_type, is_required, display_order in tpl["headers"]:
            conn.execute(_INSERT_HEADER, {
                "config_id": config_id,
                "field_name": field_name,
                "field_type": field_type,
                "is_required": is_required,
                "display_order": display_order,
            })

        for column_name, column_type, is_required, display_order in tpl["line_items"]:
            conn.execute(_INSERT_LINE_ITEM, {
                "config_id": config_id,
                "column_name": column_name,
                "column_type": column_type,
                "is_required": is_required,
                "display_order": display_order,
            })


def downgrade() -> None:
    conn = op.get_bind()
    names = [tpl["name"] for tpl in TEMPLATES]
    conn.execute(
        sa.text(
            "DELETE FROM idp_field_configurations "
            "WHERE is_template = TRUE AND org_id IS NULL AND name = ANY(:names)"
        ),
        {"names": names},
    )
