"""Add extraction prompts to the remaining global field config templates.

Mirrors what ``idp_b20_invoice_prompt_fields`` did for the Invoice template, but for
the other nine global templates seeded by ``idp_b04_seed_field_config_templates``:
PAN Card, Aadhaar Card, Purchase Order, Contract, Pay Slip, Passport, Driving Licence,
Utility Bill and Medical Report.

For each template the existing header / line-item rows are replaced with the same field
set plus an extraction ``prompt`` for every field. Idempotent: re-running replaces the
fields again. Templates that are not present in the database are skipped.

Revision ID: idp_b27_template_prompt_fields
Revises: idp_b26_roles_native
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "idp_b27_template_prompt_fields"
down_revision: Union[str, Sequence[str], None] = "idp_b26_roles_native"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (field_name, field_type, is_required, display_order, prompt)
_Header = tuple[str, str, bool, int, str]
# (column_name, column_type, is_required, display_order, prompt)
_LineItem = tuple[str, str, bool, int, str]

# ── Templates with extraction prompts ──────────────────────────────────────────
# Field sets match idp_b04 exactly; only the prompt text is added.

TEMPLATES: list[dict] = [
    {
        "name": "PAN Card",
        "headers": [
            (
                "pan_number", "text", True, 1,
                "Extract the 10-character alphanumeric Permanent Account Number (PAN). It follows "
                "the pattern of five letters, four digits and one letter (e.g. ABCDE1234F). It is "
                "usually printed prominently on the card.",
            ),
            (
                "full_name", "text", True, 2,
                "Extract the cardholder's full name as printed on the PAN card. Do not confuse it "
                "with the father's name printed below it.",
            ),
            (
                "father_name", "text", False, 3,
                "Extract the father's name printed on the PAN card. It usually appears below the "
                "cardholder's name. Leave it blank if not found.",
            ),
            (
                "date_of_birth", "date", True, 4,
                "Extract the date of birth printed on the PAN card. Formats may vary "
                "(e.g. DD/MM/YYYY).",
            ),
            (
                "signature_present", "boolean", False, 5,
                "Return true if a handwritten signature is visible on the card, otherwise false.",
            ),
        ],
        "line_items": [],
    },
    {
        "name": "Aadhaar Card",
        "headers": [
            (
                "aadhaar_number", "text", True, 1,
                "Extract the 12-digit Aadhaar number (UID). It is usually shown as three groups of "
                "four digits (e.g. 1234 5678 9012). Capture all 12 digits without spaces.",
            ),
            (
                "full_name", "text", True, 2,
                "Extract the cardholder's full name as printed on the Aadhaar card.",
            ),
            (
                "date_of_birth", "date", True, 3,
                "Extract the date of birth (DOB) printed on the Aadhaar card. Some cards show only "
                "a year of birth — capture whatever is available.",
            ),
            (
                "gender", "text", False, 4,
                "Extract the gender printed on the card (Male, Female or Other / Transgender).",
            ),
            (
                "address", "text", True, 5,
                "Extract the full address printed on the back of the Aadhaar card. It can span "
                "multiple lines — capture the complete address.",
            ),
            (
                "pincode", "text", False, 6,
                "Extract the 6-digit postal PIN code from the address. Leave it blank if not found.",
            ),
            (
                "qr_data", "text", False, 7,
                "Extract any data decoded from the QR code on the card if available. Leave it blank "
                "if not present.",
            ),
        ],
        "line_items": [],
    },
    {
        "name": "Purchase Order",
        "headers": [
            (
                "po_number", "text", True, 1,
                "Extract the Purchase Order (PO) number. It is the unique identifier of the order, "
                "usually labelled PO No., Order No. or Purchase Order Number.",
            ),
            (
                "po_date", "date", True, 2,
                "Extract the date the purchase order was issued. Formats may vary.",
            ),
            (
                "buyer_name", "text", True, 3,
                "Extract the name of the buyer or purchasing organisation that raised the PO. It is "
                "usually shown at the top of the document.",
            ),
            (
                "supplier_name", "text", True, 4,
                "Extract the name of the supplier or vendor the order is addressed to.",
            ),
            (
                "delivery_date", "date", False, 5,
                "Extract the requested or expected delivery date. Leave it blank if not found.",
            ),
            (
                "delivery_terms", "text", False, 6,
                "Extract the delivery or shipping terms (e.g. FOB, CIF, Ex-Works, Net 30). Leave it "
                "blank if not found.",
            ),
            (
                "total_amount", "number", True, 7,
                "Extract the total order amount including taxes. Formats may vary.",
            ),
            (
                "currency", "text", False, 8,
                "Extract the currency of the amounts (e.g. INR, USD) without the symbol.",
            ),
        ],
        "line_items": [
            (
                "item_code", "text", False, 1,
                "Extract the item, part or SKU code for the line. Leave it blank if not found.",
            ),
            (
                "description", "text", True, 2,
                "Extract the description of the goods or services for the line. It can span "
                "multiple lines.",
            ),
            (
                "quantity", "number", True, 3,
                "Extract the ordered quantity for the line item.",
            ),
            (
                "unit", "text", False, 4,
                "Extract the unit of measure for the line (e.g. kg, pcs, hrs, litre). Leave it "
                "blank if not found.",
            ),
            (
                "unit_price", "number", True, 5,
                "Extract the price per unit for the line item. It must not be the same as the "
                "quantity.",
            ),
            (
                "line_total", "number", True, 6,
                "Extract the total amount for the line (usually quantity multiplied by unit price).",
            ),
        ],
    },
    {
        "name": "Contract",
        "headers": [
            (
                "contract_title", "text", True, 1,
                "Extract the title or heading of the contract or agreement (e.g. 'Service "
                "Agreement', 'Master Services Agreement').",
            ),
            (
                "party_one", "text", True, 2,
                "Extract the name of the first party to the contract. It is usually introduced "
                "near the start of the agreement.",
            ),
            (
                "party_two", "text", True, 3,
                "Extract the name of the second party to the contract. Do not confuse it with the "
                "first party.",
            ),
            (
                "effective_date", "date", True, 4,
                "Extract the effective or commencement date of the contract. Formats may vary.",
            ),
            (
                "expiry_date", "date", False, 5,
                "Extract the expiry, termination or end date of the contract. Leave it blank if "
                "the contract has no fixed end date.",
            ),
            (
                "governing_law", "text", False, 6,
                "Extract the governing law or jurisdiction clause (the state or country whose laws "
                "govern the agreement). Leave it blank if not found.",
            ),
            (
                "contract_value", "number", False, 7,
                "Extract the total monetary value of the contract if stated. Leave it blank if not "
                "found.",
            ),
            (
                "signature_present", "boolean", False, 8,
                "Return true if signatures of the parties are visible on the document, otherwise "
                "false.",
            ),
        ],
        "line_items": [],
    },
    {
        "name": "Pay Slip",
        "headers": [
            (
                "employee_name", "text", True, 1,
                "Extract the full name of the employee the pay slip belongs to.",
            ),
            (
                "employee_id", "text", False, 2,
                "Extract the employee ID, code or number. Leave it blank if not found.",
            ),
            (
                "designation", "text", False, 3,
                "Extract the employee's designation, job title or role. Leave it blank if not found.",
            ),
            (
                "pay_period", "text", True, 4,
                "Extract the pay period the slip covers (e.g. 'June 2026', '01-Jun to 30-Jun').",
            ),
            (
                "basic_salary", "number", True, 5,
                "Extract the basic salary component for the period.",
            ),
            (
                "gross_salary", "number", True, 6,
                "Extract the gross salary (total earnings before deductions) for the period.",
            ),
            (
                "total_deductions", "number", False, 7,
                "Extract the total of all deductions for the period (e.g. tax, PF, insurance). "
                "Leave it blank if not found.",
            ),
            (
                "net_salary", "number", True, 8,
                "Extract the net salary or take-home pay (gross salary minus deductions).",
            ),
        ],
        "line_items": [
            (
                "component", "text", True, 1,
                "Extract the name of the salary component or line (e.g. Basic, HRA, Provident "
                "Fund, Income Tax).",
            ),
            (
                "type", "text", False, 2,
                "Extract whether the component is an earning or a deduction. Leave it blank if not "
                "clear.",
            ),
            (
                "amount", "number", True, 3,
                "Extract the amount for this salary component.",
            ),
        ],
    },
    {
        "name": "Passport",
        "headers": [
            (
                "passport_number", "text", True, 1,
                "Extract the passport number. It is an alphanumeric identifier usually printed at "
                "the top of the data page and repeated in the MRZ at the bottom.",
            ),
            (
                "surname", "text", True, 2,
                "Extract the surname / family name of the passport holder.",
            ),
            (
                "given_names", "text", True, 3,
                "Extract the given names / first names of the passport holder. Do not include the "
                "surname.",
            ),
            (
                "nationality", "text", True, 4,
                "Extract the nationality of the holder as printed on the data page.",
            ),
            (
                "date_of_birth", "date", True, 5,
                "Extract the holder's date of birth. Formats may vary.",
            ),
            (
                "gender", "text", False, 6,
                "Extract the holder's sex / gender (M, F or X). Leave it blank if not found.",
            ),
            (
                "issue_date", "date", True, 7,
                "Extract the date the passport was issued.",
            ),
            (
                "expiry_date", "date", True, 8,
                "Extract the date the passport expires (date of expiry).",
            ),
            (
                "mrz_line1", "text", False, 9,
                "Extract the first line of the machine-readable zone (MRZ) at the bottom of the "
                "data page, including the '<' filler characters. Leave it blank if not found.",
            ),
            (
                "mrz_line2", "text", False, 10,
                "Extract the second line of the machine-readable zone (MRZ) at the bottom of the "
                "data page, including the '<' filler characters. Leave it blank if not found.",
            ),
        ],
        "line_items": [],
    },
    {
        "name": "Driving Licence",
        "headers": [
            (
                "licence_number", "text", True, 1,
                "Extract the driving licence number / DL No. It is the unique alphanumeric "
                "identifier of the licence.",
            ),
            (
                "full_name", "text", True, 2,
                "Extract the full name of the licence holder.",
            ),
            (
                "date_of_birth", "date", True, 3,
                "Extract the holder's date of birth. Formats may vary.",
            ),
            (
                "address", "text", False, 4,
                "Extract the holder's address. It can span multiple lines. Leave it blank if not "
                "found.",
            ),
            (
                "issue_date", "date", True, 5,
                "Extract the date the licence was issued (date of issue).",
            ),
            (
                "expiry_date", "date", True, 6,
                "Extract the date the licence is valid until (valid till / date of expiry).",
            ),
            (
                "vehicle_class", "text", False, 7,
                "Extract the class(es) of vehicle the holder is authorised to drive (e.g. LMV, "
                "MCWG, HMV). Leave it blank if not found.",
            ),
            (
                "issuing_authority", "text", False, 8,
                "Extract the issuing authority or RTO that issued the licence. Leave it blank if "
                "not found.",
            ),
        ],
        "line_items": [],
    },
    {
        "name": "Utility Bill",
        "headers": [
            (
                "provider_name", "text", True, 1,
                "Extract the name of the utility provider or company that issued the bill (e.g. "
                "the electricity, water or gas company).",
            ),
            (
                "customer_name", "text", True, 2,
                "Extract the name of the customer the bill is addressed to.",
            ),
            (
                "account_number", "text", False, 3,
                "Extract the customer account or consumer number. Leave it blank if not found.",
            ),
            (
                "bill_date", "date", True, 4,
                "Extract the date the bill was issued. Formats may vary.",
            ),
            (
                "due_date", "date", False, 5,
                "Extract the payment due date. Leave it blank if not found.",
            ),
            (
                "billing_period", "text", False, 6,
                "Extract the billing period the bill covers (e.g. '01-May to 31-May 2026'). Leave "
                "it blank if not found.",
            ),
            (
                "units_consumed", "number", False, 7,
                "Extract the number of units consumed during the billing period (e.g. kWh of "
                "electricity, litres of water). Leave it blank if not found.",
            ),
            (
                "total_amount", "number", True, 8,
                "Extract the total amount due on the bill including all charges and taxes.",
            ),
        ],
        "line_items": [],
    },
    {
        "name": "Medical Report",
        "headers": [
            (
                "patient_name", "text", True, 1,
                "Extract the full name of the patient the report belongs to.",
            ),
            (
                "patient_id", "text", False, 2,
                "Extract the patient ID, MRN or registration number. Leave it blank if not found.",
            ),
            (
                "date_of_birth", "date", False, 3,
                "Extract the patient's date of birth if stated. Leave it blank if not found.",
            ),
            (
                "report_date", "date", True, 4,
                "Extract the date the report was generated or the sample was collected. Formats "
                "may vary.",
            ),
            (
                "report_type", "text", True, 5,
                "Extract the type of report or test panel (e.g. 'Complete Blood Count', 'Lipid "
                "Profile', 'X-Ray Chest').",
            ),
            (
                "referring_doctor", "text", False, 6,
                "Extract the name of the referring or consulting doctor. Leave it blank if not "
                "found.",
            ),
            (
                "lab_name", "text", False, 7,
                "Extract the name of the laboratory or diagnostic centre that issued the report. "
                "Leave it blank if not found.",
            ),
        ],
        "line_items": [
            (
                "test_name", "text", True, 1,
                "Extract the name of the individual test or parameter measured (e.g. Haemoglobin, "
                "Cholesterol).",
            ),
            (
                "result", "text", True, 2,
                "Extract the measured result or value for the test.",
            ),
            (
                "reference", "text", False, 3,
                "Extract the normal reference range for the test (e.g. '13.0 - 17.0'). Leave it "
                "blank if not found.",
            ),
            (
                "unit", "text", False, 4,
                "Extract the unit of measurement for the result (e.g. g/dL, mg/dL). Leave it blank "
                "if not found.",
            ),
            (
                "status", "text", False, 5,
                "Extract the flag or status of the result if shown (e.g. Normal, High, Low, "
                "Abnormal). Leave it blank if not found.",
            ),
        ],
    },
]

# Original idp_b04 field sets (no prompts) used to restore on downgrade.
_OLD_TEMPLATES: dict[str, dict] = {
    "PAN Card": {
        "headers": [
            ("pan_number", "text", True, 1),
            ("full_name", "text", True, 2),
            ("father_name", "text", False, 3),
            ("date_of_birth", "date", True, 4),
            ("signature_present", "boolean", False, 5),
        ],
        "line_items": [],
    },
    "Aadhaar Card": {
        "headers": [
            ("aadhaar_number", "text", True, 1),
            ("full_name", "text", True, 2),
            ("date_of_birth", "date", True, 3),
            ("gender", "text", False, 4),
            ("address", "text", True, 5),
            ("pincode", "text", False, 6),
            ("qr_data", "text", False, 7),
        ],
        "line_items": [],
    },
    "Purchase Order": {
        "headers": [
            ("po_number", "text", True, 1),
            ("po_date", "date", True, 2),
            ("buyer_name", "text", True, 3),
            ("supplier_name", "text", True, 4),
            ("delivery_date", "date", False, 5),
            ("delivery_terms", "text", False, 6),
            ("total_amount", "number", True, 7),
            ("currency", "text", False, 8),
        ],
        "line_items": [
            ("item_code", "text", False, 1),
            ("description", "text", True, 2),
            ("quantity", "number", True, 3),
            ("unit", "text", False, 4),
            ("unit_price", "number", True, 5),
            ("line_total", "number", True, 6),
        ],
    },
    "Contract": {
        "headers": [
            ("contract_title", "text", True, 1),
            ("party_one", "text", True, 2),
            ("party_two", "text", True, 3),
            ("effective_date", "date", True, 4),
            ("expiry_date", "date", False, 5),
            ("governing_law", "text", False, 6),
            ("contract_value", "number", False, 7),
            ("signature_present", "boolean", False, 8),
        ],
        "line_items": [],
    },
    "Pay Slip": {
        "headers": [
            ("employee_name", "text", True, 1),
            ("employee_id", "text", False, 2),
            ("designation", "text", False, 3),
            ("pay_period", "text", True, 4),
            ("basic_salary", "number", True, 5),
            ("gross_salary", "number", True, 6),
            ("total_deductions", "number", False, 7),
            ("net_salary", "number", True, 8),
        ],
        "line_items": [
            ("component", "text", True, 1),
            ("type", "text", False, 2),
            ("amount", "number", True, 3),
        ],
    },
    "Passport": {
        "headers": [
            ("passport_number", "text", True, 1),
            ("surname", "text", True, 2),
            ("given_names", "text", True, 3),
            ("nationality", "text", True, 4),
            ("date_of_birth", "date", True, 5),
            ("gender", "text", False, 6),
            ("issue_date", "date", True, 7),
            ("expiry_date", "date", True, 8),
            ("mrz_line1", "text", False, 9),
            ("mrz_line2", "text", False, 10),
        ],
        "line_items": [],
    },
    "Driving Licence": {
        "headers": [
            ("licence_number", "text", True, 1),
            ("full_name", "text", True, 2),
            ("date_of_birth", "date", True, 3),
            ("address", "text", False, 4),
            ("issue_date", "date", True, 5),
            ("expiry_date", "date", True, 6),
            ("vehicle_class", "text", False, 7),
            ("issuing_authority", "text", False, 8),
        ],
        "line_items": [],
    },
    "Utility Bill": {
        "headers": [
            ("provider_name", "text", True, 1),
            ("customer_name", "text", True, 2),
            ("account_number", "text", False, 3),
            ("bill_date", "date", True, 4),
            ("due_date", "date", False, 5),
            ("billing_period", "text", False, 6),
            ("units_consumed", "number", False, 7),
            ("total_amount", "number", True, 8),
        ],
        "line_items": [],
    },
    "Medical Report": {
        "headers": [
            ("patient_name", "text", True, 1),
            ("patient_id", "text", False, 2),
            ("date_of_birth", "date", False, 3),
            ("report_date", "date", True, 4),
            ("report_type", "text", True, 5),
            ("referring_doctor", "text", False, 6),
            ("lab_name", "text", False, 7),
        ],
        "line_items": [
            ("test_name", "text", True, 1),
            ("result", "text", True, 2),
            ("reference", "text", False, 3),
            ("unit", "text", False, 4),
            ("status", "text", False, 5),
        ],
    },
}

# ── SQL statements (mirror idp_b20) ────────────────────────────────────────────

_GET_TEMPLATE_CONFIG_ID = sa.text(
    "SELECT id FROM idp_field_configurations "
    "WHERE name = :name AND org_id IS NULL AND is_template = TRUE "
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


def upgrade() -> None:
    conn = op.get_bind()

    for tpl in TEMPLATES:
        row = conn.execute(_GET_TEMPLATE_CONFIG_ID, {"name": tpl["name"]}).fetchone()
        if not row:
            continue  # Template not present in this database; skip.

        config_id = row[0]
        conn.execute(_DELETE_HEADERS, {"config_id": config_id})
        conn.execute(_DELETE_LINE_ITEMS, {"config_id": config_id})

        for field_name, field_type, is_required, display_order, prompt in tpl["headers"]:
            conn.execute(_INSERT_HEADER, {
                "config_id": config_id,
                "field_name": field_name,
                "field_type": field_type,
                "is_required": is_required,
                "display_order": display_order,
                "prompt": prompt,
            })

        for column_name, column_type, is_required, display_order, prompt in tpl["line_items"]:
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

    for name, tpl in _OLD_TEMPLATES.items():
        row = conn.execute(_GET_TEMPLATE_CONFIG_ID, {"name": name}).fetchone()
        if not row:
            continue

        config_id = row[0]
        conn.execute(_DELETE_HEADERS, {"config_id": config_id})
        conn.execute(_DELETE_LINE_ITEMS, {"config_id": config_id})

        for field_name, field_type, is_required, display_order in tpl["headers"]:
            conn.execute(_INSERT_HEADER_NO_PROMPT, {
                "config_id": config_id,
                "field_name": field_name,
                "field_type": field_type,
                "is_required": is_required,
                "display_order": display_order,
            })

        for column_name, column_type, is_required, display_order in tpl["line_items"]:
            conn.execute(_INSERT_LINE_ITEM_NO_PROMPT, {
                "config_id": config_id,
                "column_name": column_name,
                "column_type": column_type,
                "is_required": is_required,
                "display_order": display_order,
            })
