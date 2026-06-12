"""Add prompt column to field config tables; replace Invoice template with full field set.

Adds a nullable TEXT ``prompt`` column to both ``idp_field_config_headers`` and
``idp_field_config_line_items``, then replaces the global Invoice template fields with
the full production field list (header + line-item fields) including extraction prompts.

Revision ID: idp_b20_invoice_prompt_fields
Revises: idp_b19_job_log
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "idp_b20_invoice_prompt_fields"
down_revision: Union[str, Sequence[str], None] = "idp_b19_job_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (field_name, field_type, is_required, display_order, prompt)
_Header = tuple[str, str, bool, int, str]
# (column_name, column_type, is_required, display_order, prompt)
_LineItem = tuple[str, str, bool, int, str]

INVOICE_HEADERS: list[_Header] = [
    (
        "po_number", "text", False, 1,
        "Extract the purchase order number or PO no. or PO/WO/FO no. number from the invoice. "
        "It generally 4 to 8 digit numeric number. Don't get confused with PO number and Invoice "
        "number both are different. If you don't found PO no. leave it blank.",
    ),
    (
        "invoice_number", "text", True, 2,
        "Extract the unique invoice number. Genrally it is alphanumric number. Don't get confused "
        "with Invoice number and PO number and both are different. If invoice number is not found "
        "leave it blank.",
    ),
    (
        "invoice_date", "date", True, 3,
        "The date the invoice was issued. Formats may vary.",
    ),
    (
        "currency", "text", False, 4,
        "The currency used in the invoice (e.g., INR or Dollar) without the symbol.",
    ),
    (
        "supplier_name", "text", True, 5,
        "Extract the supplier name. The supplier is the vendor that issued the invoice. Analyze the "
        "invoice content to identify the name of the supplier or vendor providing the goods or "
        "services. This name is usually located near the top of the invoice.",
    ),
    (
        "supplier_gstn", "text", False, 6,
        "Extract the GSTIN Number of the supplier from the invoice. The supplier is the vendor that "
        "issued the invoice. If no supplier GSTIN is found, leave it blank.",
    ),
    (
        "supplier_city", "text", False, 7,
        "Extract only city of the supplier. Don't add PIN in city name. If no city name is found, "
        "leave it blank.",
    ),
    (
        "supplier_state", "text", False, 8,
        "The state of the supplier. It should be the name of an Indian state. If no state name is "
        "found, leave it blank.",
    ),
    (
        "invoice_taxable_value", "number", False, 9,
        "The taxable value stated in the invoice excluding taxes. It also called total value found "
        "in last of invoice. Formats may vary.",
    ),
    (
        "gst_tax_type", "text", False, 10,
        "Extract all GST type that are used to calculate the GST from the invoice. Don't put Tax "
        "percentage in it. If no GST is found, leave it blank.",
    ),
    (
        "gst_rate", "text", False, 11,
        "Extract GST rate that are used to calculate the GST from the invoice.",
    ),
    (
        "delivery_gstn_no", "text", False, 12,
        "The GSTIN for the delivery location or buyer or Billed to or ship to or customer. This "
        "generally under customer or ship to details. Formats may vary.",
    ),
    (
        "invoice_value", "number", True, 13,
        "The total invoice value including taxes. Formats may vary.",
    ),
    (
        "supplier_pan", "text", False, 14,
        "Extract the supplier's PAN. Don't extract buyer or Billed to or ship to or customer PAN "
        "no. or supplier GSTIN or delivery GSTIN from invoice. Do not include TAN no. in it. "
        "Leave it blank if not found.",
    ),
    (
        "country", "text", False, 15,
        "The country name of the supplier. Formats may vary.",
    ),
    (
        "pwc_entity_name", "text", False, 16,
        "The name of the PwC entity involved in the transaction. Formats may vary.",
    ),
    (
        "irn", "text", False, 17,
        "Extract the Invoice Reference Number (IRN). It may be split across multiple lines or may "
        "be separated by space try to capture full irn no. Its length is generally 64. Its very "
        "important to capture it.",
    ),
    (
        "delivery_location", "text", False, 18,
        "The delivery location (city or state name).",
    ),
    (
        "bill_to_address", "text", False, 19,
        "The bill to address. It can span multiple lines. Its comes under supplier name generally. "
        "Leave it blank if not found.",
    ),
    (
        "ship_to_address", "text", False, 20,
        "Extract the ship to or customer address. It can span multiple lines. Leave it blank if "
        "not found.",
    ),
    (
        "account_number", "text", False, 21,
        "The account number. Leave blank if not found.",
    ),
]

INVOICE_LINE_ITEMS: list[_LineItem] = [
    (
        "description", "text", True, 1,
        "The description or particulars of the items or details of the items in the invoice. It "
        "can be of multiple lines. Its generally description of goods or services.",
    ),
    (
        "resource_name", "text", False, 2,
        "The name of the employee/candidate or anyone mentioned in the invoice. This should be a "
        "person's name.",
    ),
    (
        "quantity", "number", False, 3,
        "The quantity of items or services listed in the invoice.",
    ),
    (
        "amount", "number", True, 4,
        "The total amount for the items or services listed in the invoice.",
    ),
    (
        "unit_price", "number", False, 5,
        "The unit price for each item or service mentioned in the invoice. It can not be same as "
        "the quantity.",
    ),
    (
        "hsn_code", "text", False, 6,
        "The HSN/SAC code for the items or services in the invoice. It is generally a single "
        "numeric field written under HSN Column in table. It can be written within description "
        "with hsn tag. Include number in it and do not ignore Zero.",
    ),
    (
        "igst_rate", "number", False, 7,
        "Extract the IGST rate only from the invoice. Make sure it is labeled as IGST and is the "
        "correct rate associated with the IGST amount. Don't extract any other GST rate.",
    ),
    (
        "igst_value", "number", False, 8,
        "Extract the IGST amount or value only from the invoice. Ensure it is the correct value "
        "associated with the IGST rate. Don't extract any other GST value.",
    ),
    (
        "sgst_rate", "number", False, 9,
        "Extract the SGST rate only applicable from the invoice. Make sure it is labeled as SGST "
        "and is the correct rate associated with the SGST amount. Don't extract any other GST rate.",
    ),
    (
        "sgst_value", "number", False, 10,
        "Extract the SGST amount or value only from the invoice. Ensure it is the correct value "
        "associated with the SGST rate. Don't extract any other GST value.",
    ),
    (
        "cgst_rate", "number", False, 11,
        "Extract the CGST rate only applicable from the invoice. Make sure it is labeled as CGST "
        "and is the correct rate associated with the CGST amount. Don't extract any other GST rate.",
    ),
    (
        "cgst_value", "number", False, 12,
        "Extract the CGST amount or value only from the invoice. Ensure it is the correct value "
        "associated with the CGST rate. Don't extract any other GST value.",
    ),
    (
        "utgst_rate", "number", False, 13,
        "Extract the UTGST rate only applicable from the invoice. Make sure it is labeled as UTGST "
        "and is the correct rate associated with the UTGST amount. Don't extract any other GST rate.",
    ),
    (
        "utgst_value", "number", False, 14,
        "Extract the UTGST amount or value only from the invoice. Ensure it is the correct value "
        "associated with the UTGST rate. Don't extract any other GST value.",
    ),
    (
        "gst_category", "text", False, 15,
        "All GST categories applicable from the invoice details (e.g., IGST, SGST, CGST, UTGST).",
    ),
    (
        "expense_start_date", "date", False, 16,
        "The expense start date from the invoice description. Its likes Start Date, Date of joining "
        "or Billing Period for the Month.",
    ),
    (
        "expense_end_date", "date", False, 17,
        "The expense end date from the invoice description. Its likes Due Date or Billing Month / "
        "Period to.",
    ),
    (
        "vat_category", "text", False, 18,
        "The VAT category from the invoice details.",
    ),
    (
        "vat_rate", "number", False, 19,
        "The VAT rate from the invoice details.",
    ),
    (
        "supplier_pan", "text", False, 20,
        "The supplier's PAN. Don't give customer PAN no. if you don't get it leave it blank if not "
        "found.",
    ),
    (
        "unit", "text", False, 21,
        "The unit of the goods (e.g., kg, pak, mg, liter). Leave it blank if not found.",
    ),
]

# Old invoice fields used in downgrade (from idp_b04)
_OLD_INVOICE_HEADERS: list[tuple[str, str, bool, int]] = [
    ("invoice_number", "text",   True,  1),
    ("invoice_date",   "date",   True,  2),
    ("vendor_name",    "text",   True,  3),
    ("vendor_address", "text",   False, 4),
    ("buyer_name",     "text",   False, 5),
    ("due_date",       "date",   False, 6),
    ("subtotal",       "number", True,  7),
    ("tax_amount",     "number", False, 8),
    ("total_amount",   "number", True,  9),
    ("currency",       "text",   False, 10),
]

_OLD_INVOICE_LINE_ITEMS: list[tuple[str, str, bool, int]] = [
    ("description", "text",   True,  1),
    ("quantity",    "number", True,  2),
    ("unit_price",  "number", True,  3),
    ("tax_rate",    "number", False, 4),
    ("line_total",  "number", True,  5),
]

_GET_INVOICE_CONFIG_ID = sa.text(
    "SELECT id FROM idp_field_configurations "
    "WHERE name = 'Invoice' AND org_id IS NULL AND is_template = TRUE "
    "LIMIT 1"
)

_DELETE_HEADERS = sa.text(
    "DELETE FROM idp_field_config_headers WHERE config_id = :config_id"
)

_DELETE_LINE_ITEMS = sa.text(
    "DELETE FROM idp_field_config_line_items WHERE config_id = :config_id"
)

_INSERT_HEADER = sa.text(
    "INSERT INTO idp_field_config_headers "
    "(id, config_id, field_name, field_type, is_required, display_order, prompt, created_at, updated_at) "
    "VALUES (gen_random_uuid(), :config_id, :field_name, :field_type, :is_required, :display_order, "
    ":prompt, now(), now())"
)

_INSERT_LINE_ITEM = sa.text(
    "INSERT INTO idp_field_config_line_items "
    "(id, config_id, column_name, column_type, is_required, display_order, prompt, created_at, updated_at) "
    "VALUES (gen_random_uuid(), :config_id, :column_name, :column_type, :is_required, :display_order, "
    ":prompt, now(), now())"
)

_INSERT_HEADER_NO_PROMPT = sa.text(
    "INSERT INTO idp_field_config_headers "
    "(id, config_id, field_name, field_type, is_required, display_order, created_at, updated_at) "
    "VALUES (gen_random_uuid(), :config_id, :field_name, :field_type, :is_required, :display_order, "
    "now(), now())"
)

_INSERT_LINE_ITEM_NO_PROMPT = sa.text(
    "INSERT INTO idp_field_config_line_items "
    "(id, config_id, column_name, column_type, is_required, display_order, created_at, updated_at) "
    "VALUES (gen_random_uuid(), :config_id, :column_name, :column_type, :is_required, :display_order, "
    "now(), now())"
)


def _column_exists(conn: sa.engine.Connection, table: str, column: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column LIMIT 1"
        ),
        {"table": table, "column": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add prompt column to headers (idempotent)
    if not _column_exists(conn, "idp_field_config_headers", "prompt"):
        op.add_column("idp_field_config_headers", sa.Column("prompt", sa.Text(), nullable=True))

    # 2. Add prompt column to line items (idempotent)
    if not _column_exists(conn, "idp_field_config_line_items", "prompt"):
        op.add_column("idp_field_config_line_items", sa.Column("prompt", sa.Text(), nullable=True))

    # 3. Replace Invoice template fields
    row = conn.execute(_GET_INVOICE_CONFIG_ID).fetchone()
    if not row:
        return  # Invoice template not found; nothing to update

    config_id = row[0]

    conn.execute(_DELETE_HEADERS, {"config_id": config_id})
    conn.execute(_DELETE_LINE_ITEMS, {"config_id": config_id})

    for field_name, field_type, is_required, display_order, prompt in INVOICE_HEADERS:
        conn.execute(_INSERT_HEADER, {
            "config_id": config_id,
            "field_name": field_name,
            "field_type": field_type,
            "is_required": is_required,
            "display_order": display_order,
            "prompt": prompt,
        })

    for column_name, column_type, is_required, display_order, prompt in INVOICE_LINE_ITEMS:
        conn.execute(_INSERT_LINE_ITEM, {
            "config_id": config_id,
            "column_name": column_name,
            "column_type": column_type,
            "is_required": is_required,
            "display_order": display_order,
            "prompt": prompt,
        })


def downgrade() -> None:
    conn = op.get_bind()

    # Restore old Invoice template fields
    row = conn.execute(_GET_INVOICE_CONFIG_ID).fetchone()
    if row:
        config_id = row[0]
        conn.execute(_DELETE_HEADERS, {"config_id": config_id})
        conn.execute(_DELETE_LINE_ITEMS, {"config_id": config_id})

        for field_name, field_type, is_required, display_order in _OLD_INVOICE_HEADERS:
            conn.execute(_INSERT_HEADER_NO_PROMPT, {
                "config_id": config_id,
                "field_name": field_name,
                "field_type": field_type,
                "is_required": is_required,
                "display_order": display_order,
            })

        for column_name, column_type, is_required, display_order in _OLD_INVOICE_LINE_ITEMS:
            conn.execute(_INSERT_LINE_ITEM_NO_PROMPT, {
                "config_id": config_id,
                "column_name": column_name,
                "column_type": column_type,
                "is_required": is_required,
                "display_order": display_order,
            })

    # Drop prompt columns
    op.drop_column("idp_field_config_line_items", "prompt")
    op.drop_column("idp_field_config_headers", "prompt")
