"""Backfill extraction prompts on the catalogue-seeded global templates.

The startup catalogue seeder (``services/idp/catalogue.py``) creates six global
templates that the ``idp_b04`` migration does not: Cheque, Contract Agreement,
Goods Receipt Note, Offer Letter, Salary Slip and Voter ID. On existing databases
these were seeded before prompts were defined, and the seeder only inserts new
templates, so it never backfills them.

This migration sets the ``prompt`` for each field of those six templates by field
name, leaving every other column (including ``description``) untouched. Idempotent
and safe to re-run. Templates / fields not present are simply not updated.

Revision ID: idp_b28_catalogue_prompt_fields
Revises: idp_b27_template_prompt_fields
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "idp_b28_catalogue_prompt_fields"
down_revision: Union[str, Sequence[str], None] = "idp_b27_template_prompt_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# template name -> {"headers": {field_name: prompt}, "line_items": {column_name: prompt}}
PROMPTS: dict[str, dict[str, dict[str, str]]] = {
    "Goods Receipt Note": {
        "headers": {
            "grn_number": "Extract the Goods Receipt Note (GRN) number — the unique identifier of the receipt document, usually labelled GRN No. or Receipt No.",
            "received_date": "Extract the date the goods were received. Formats may vary.",
            "vendor_name": "Extract the name of the supplying vendor who delivered the goods.",
            "po_reference": "Extract the Purchase Order (PO) number this goods receipt refers to. Leave it blank if not found.",
        },
        "line_items": {
            "description": "Extract the description of the goods received for the line. It can span multiple lines.",
            "qty_received": "Extract the quantity of goods actually received for the line item.",
        },
    },
    "Cheque": {
        "headers": {
            "bank_name": "Extract the name of the bank that issued the cheque, usually printed prominently at the top.",
            "cheque_number": "Extract the cheque number. It is a numeric identifier printed on the cheque, often in the MICR line at the bottom.",
            "cheque_date": "Extract the date written on the cheque. Formats may vary.",
            "payee_name": "Extract the name of the payee — the person or organisation the cheque is made out to (the 'Pay to' line).",
            "amount": "Extract the cheque amount in figures. If only the amount in words is present, convert it to a number.",
        },
        "line_items": {},
    },
    "Voter ID": {
        "headers": {
            "voter_card_number": "Extract the unique voter ID / EPIC number printed on the card. It is usually an alphanumeric code.",
            "full_name": "Extract the full name of the voter as printed on the card.",
            "father_or_spouse_name": "Extract the father's or husband's/spouse's name listed on the card. Leave it blank if not found.",
            "date_of_birth": "Extract the voter's date of birth or age if shown. Leave it blank if not found.",
        },
        "line_items": {},
    },
    "Contract Agreement": {
        "headers": {
            "party_a": "Extract the name of the first party (Party A) to the agreement, usually introduced near the start.",
            "party_b": "Extract the name of the second party (Party B) to the agreement. Do not confuse it with Party A.",
            "effective_date": "Extract the effective or commencement date of the agreement. Formats may vary.",
            "termination_date": "Extract the termination, expiry or end date of the agreement. Leave it blank if there is no fixed end date.",
            "governing_law": "Extract the governing law or jurisdiction — the state or country whose laws govern the agreement. Leave it blank if not found.",
        },
        "line_items": {},
    },
    "Offer Letter": {
        "headers": {
            "candidate_name": "Extract the full name of the candidate or applicant the offer is addressed to.",
            "job_title": "Extract the job title or position being offered.",
            "joining_date": "Extract the proposed date of joining / start date. Formats may vary.",
            "salary_offered": "Extract the offered gross / base salary as a number. Leave it blank if not stated.",
        },
        "line_items": {},
    },
    "Salary Slip": {
        "headers": {
            "employer_name": "Extract the name of the employer or company that issued the pay slip.",
            "employee_name": "Extract the full name of the employee the pay slip belongs to.",
            "pay_period": "Extract the pay period the slip covers (e.g. 'June 2026', '01-Jun to 30-Jun').",
            "gross_salary": "Extract the gross salary (total earnings before deductions) for the period.",
            "deductions": "Extract the total of all deductions for the period (e.g. tax, PF, insurance). Leave it blank if not found.",
            "net_salary": "Extract the net salary or take-home pay (gross salary minus deductions).",
        },
        "line_items": {
            "component_name": "Extract the name of the salary component or line (e.g. Basic, HRA, Provident Fund, Income Tax).",
            "component_amount": "Extract the amount for this salary component.",
        },
    },
}

_GET_TEMPLATE_CONFIG_ID = sa.text(
    "SELECT id FROM idp_field_configurations "
    "WHERE name = :name AND org_id IS NULL AND is_template = TRUE "
    "LIMIT 1"
)

_UPDATE_HEADER_PROMPT = sa.text(
    "UPDATE idp_field_config_headers SET prompt = :prompt, updated_at = now() "
    "WHERE config_id = :config_id AND field_name = :field_name"
)

_UPDATE_LINE_ITEM_PROMPT = sa.text(
    "UPDATE idp_field_config_line_items SET prompt = :prompt, updated_at = now() "
    "WHERE config_id = :config_id AND column_name = :column_name"
)

_CLEAR_HEADER_PROMPT = sa.text(
    "UPDATE idp_field_config_headers SET prompt = NULL, updated_at = now() "
    "WHERE config_id = :config_id AND field_name = :field_name"
)

_CLEAR_LINE_ITEM_PROMPT = sa.text(
    "UPDATE idp_field_config_line_items SET prompt = NULL, updated_at = now() "
    "WHERE config_id = :config_id AND column_name = :column_name"
)


def upgrade() -> None:
    conn = op.get_bind()

    for name, fields in PROMPTS.items():
        row = conn.execute(_GET_TEMPLATE_CONFIG_ID, {"name": name}).fetchone()
        if not row:
            continue  # Template not present in this database; skip.

        config_id = row[0]
        for field_name, prompt in fields["headers"].items():
            conn.execute(_UPDATE_HEADER_PROMPT, {
                "config_id": config_id,
                "field_name": field_name,
                "prompt": prompt,
            })
        for column_name, prompt in fields["line_items"].items():
            conn.execute(_UPDATE_LINE_ITEM_PROMPT, {
                "config_id": config_id,
                "column_name": column_name,
                "prompt": prompt,
            })


def downgrade() -> None:
    conn = op.get_bind()

    for name, fields in PROMPTS.items():
        row = conn.execute(_GET_TEMPLATE_CONFIG_ID, {"name": name}).fetchone()
        if not row:
            continue

        config_id = row[0]
        for field_name in fields["headers"]:
            conn.execute(_CLEAR_HEADER_PROMPT, {
                "config_id": config_id,
                "field_name": field_name,
            })
        for column_name in fields["line_items"]:
            conn.execute(_CLEAR_LINE_ITEM_PROMPT, {
                "config_id": config_id,
                "column_name": column_name,
            })
