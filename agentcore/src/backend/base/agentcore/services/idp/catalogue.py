from __future__ import annotations

from typing import TYPE_CHECKING
from sqlmodel import select
from loguru import logger

from agentcore.services.database.models.idp.config import (
    IdpFieldConfiguration,
    IdpFieldConfigHeader,
    IdpFieldConfigLineItem,
)

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

# 11 standard document templates
DEFAULT_TEMPLATES = [
    # --- Financial - AP / AR ---
    {
        "name": "Invoice",
        "description": "Standard invoice template for Accounts Payable processing.",
        "headers": [
            {"field_name": "vendor_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "The name of the vendor/seller"},
            {"field_name": "invoice_number", "field_type": "text", "is_required": True, "display_order": 2, "description": "The unique invoice identifier"},
            {"field_name": "invoice_date", "field_type": "date", "is_required": True, "display_order": 3, "description": "The date of the invoice"},
            {"field_name": "po_number", "field_type": "text", "is_required": False, "display_order": 4, "description": "Associated Purchase Order number"},
            {"field_name": "currency", "field_type": "text", "is_required": False, "display_order": 5, "description": "The currency of the amounts"},
            {"field_name": "tax_amount", "field_type": "number", "is_required": False, "display_order": 6, "description": "The total tax amount charged"},
            {"field_name": "total_amount", "field_type": "number", "is_required": True, "display_order": 7, "description": "The total invoice amount"},
            {"field_name": "payment_terms", "field_type": "text", "is_required": False, "display_order": 8, "description": "Payment terms (e.g. Net 30)"},
        ],
        "line_items": [
            {"column_name": "description", "column_type": "text", "is_required": True, "display_order": 1},
            {"column_name": "quantity", "column_type": "number", "is_required": False, "display_order": 2},
            {"column_name": "unit_price", "column_type": "number", "is_required": False, "display_order": 3},
            {"column_name": "line_amount", "column_type": "number", "is_required": True, "display_order": 4},
        ]
    },
    {
        "name": "Purchase Order",
        "description": "Standard procurement purchase order request.",
        "headers": [
            {"field_name": "buyer_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "The name of the purchasing organization"},
            {"field_name": "vendor_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "The name of the supplying vendor"},
            {"field_name": "po_number", "field_type": "text", "is_required": True, "display_order": 3, "description": "Purchase Order unique identifier"},
            {"field_name": "po_date", "field_type": "date", "is_required": True, "display_order": 4, "description": "Date the PO was issued"},
            {"field_name": "total_amount", "field_type": "number", "is_required": True, "display_order": 5, "description": "Total order amount limit"},
        ],
        "line_items": [
            {"column_name": "part_number", "column_type": "text", "is_required": False, "display_order": 1},
            {"column_name": "description", "column_type": "text", "is_required": True, "display_order": 2},
            {"column_name": "quantity", "column_type": "number", "is_required": True, "display_order": 3},
            {"column_name": "unit_price", "column_type": "number", "is_required": True, "display_order": 4},
        ]
    },
    {
        "name": "Goods Receipt Note",
        "description": "Delivery inspection receipt or goods inwards record.",
        "headers": [
            {"field_name": "grn_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Goods Receipt Note unique identifier"},
            {"field_name": "received_date", "field_type": "date", "is_required": True, "display_order": 2, "description": "Date goods were received"},
            {"field_name": "vendor_name", "field_type": "text", "is_required": True, "display_order": 3, "description": "Supplying vendor's name"},
            {"field_name": "po_reference", "field_type": "text", "is_required": False, "display_order": 4, "description": "Purchase Order reference number"},
        ],
        "line_items": [
            {"column_name": "description", "column_type": "text", "is_required": True, "display_order": 1},
            {"column_name": "qty_received", "column_type": "number", "is_required": True, "display_order": 2},
        ]
    },

    # --- Financial - Banking ---
    {
        "name": "Cheque",
        "description": "Standard bank cheque or check clearance details.",
        "headers": [
            {"field_name": "bank_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Bank name"},
            {"field_name": "cheque_number", "field_type": "text", "is_required": True, "display_order": 2, "description": "The unique cheque identifier"},
            {"field_name": "cheque_date", "field_type": "date", "is_required": True, "display_order": 3, "description": "Cheque issue date"},
            {"field_name": "payee_name", "field_type": "text", "is_required": True, "display_order": 4, "description": "The receiver name"},
            {"field_name": "amount", "field_type": "number", "is_required": True, "display_order": 5, "description": "Cheque transaction amount"},
        ],
        "line_items": []
    },

    # --- KYC & Identity ---
    {
        "name": "Passport",
        "description": "Global international identity travel document.",
        "headers": [
            {"field_name": "passport_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Passport reference number"},
            {"field_name": "surname", "field_type": "text", "is_required": True, "display_order": 2, "description": "Surname / Family name"},
            {"field_name": "given_names", "field_type": "text", "is_required": True, "display_order": 3, "description": "Given / First names"},
            {"field_name": "date_of_birth", "field_type": "date", "is_required": True, "display_order": 4, "description": "Date of birth"},
            {"field_name": "nationality", "field_type": "text", "is_required": True, "display_order": 5, "description": "Nationality of holder"},
            {"field_name": "expiry_date", "field_type": "date", "is_required": True, "display_order": 6, "description": "Passport expiration date"},
        ],
        "line_items": []
    },
    {
        "name": "Driving Licence",
        "description": "Government issued vehicle operators license.",
        "headers": [
            {"field_name": "licence_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Licence reference ID"},
            {"field_name": "full_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Licence holder full name"},
            {"field_name": "date_of_birth", "field_type": "date", "is_required": True, "display_order": 3, "description": "Date of birth"},
            {"field_name": "address", "field_type": "text", "is_required": False, "display_order": 4, "description": "Holder address"},
            {"field_name": "expiry_date", "field_type": "date", "is_required": True, "display_order": 5, "description": "Licence expiration date"},
        ],
        "line_items": []
    },
    {
        "name": "Voter ID",
        "description": "Electoral identification card.",
        "headers": [
            {"field_name": "voter_card_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Unique voter card ID"},
            {"field_name": "full_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Voter full name"},
            {"field_name": "father_or_spouse_name", "field_type": "text", "is_required": False, "display_order": 3, "description": "Relative name listed"},
            {"field_name": "date_of_birth", "field_type": "date", "is_required": False, "display_order": 4, "description": "Date of birth"},
        ],
        "line_items": []
    },
    {
        "name": "Utility Bill",
        "description": "Standard utility bill serving as proof of residence/address.",
        "headers": [
            {"field_name": "utility_provider", "field_type": "text", "is_required": True, "display_order": 1, "description": "Name of utility company"},
            {"field_name": "account_number", "field_type": "text", "is_required": True, "display_order": 2, "description": "Customer account number"},
            {"field_name": "bill_date", "field_type": "date", "is_required": True, "display_order": 3, "description": "Date the bill was issued"},
            {"field_name": "due_date", "field_type": "date", "is_required": False, "display_order": 4, "description": "Payment due date"},
            {"field_name": "amount_due", "field_type": "number", "is_required": True, "display_order": 5, "description": "Total amount due"},
        ],
        "line_items": []
    },

    # --- Legal & Contracts ---
    {
        "name": "Contract Agreement",
        "description": "Standard business contract or legal agreement layout.",
        "headers": [
            {"field_name": "party_a", "field_type": "text", "is_required": True, "display_order": 1, "description": "First party in agreement"},
            {"field_name": "party_b", "field_type": "text", "is_required": True, "display_order": 2, "description": "Second party in agreement"},
            {"field_name": "effective_date", "field_type": "date", "is_required": True, "display_order": 3, "description": "Agreement effective start date"},
            {"field_name": "termination_date", "field_type": "date", "is_required": False, "display_order": 4, "description": "Agreement termination/expiry date"},
            {"field_name": "governing_law", "field_type": "text", "is_required": False, "display_order": 5, "description": "State or country law governing agreement"},
        ],
        "line_items": []
    },

    # --- HR & Payroll ---
    {
        "name": "Offer Letter",
        "description": "Standard candidate employment job offer letter.",
        "headers": [
            {"field_name": "candidate_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Name of applicant/candidate"},
            {"field_name": "job_title", "field_type": "text", "is_required": True, "display_order": 2, "description": "Proposed job title"},
            {"field_name": "joining_date", "field_type": "date", "is_required": True, "display_order": 3, "description": "Proposed date of joining"},
            {"field_name": "salary_offered", "field_type": "number", "is_required": False, "display_order": 4, "description": "Offered gross base salary"},
        ],
        "line_items": []
    },
    {
        "name": "Salary Slip",
        "description": "Periodic payroll payslip detail breakdown.",
        "headers": [
            {"field_name": "employer_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Employer/Company name"},
            {"field_name": "employee_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Employee full name"},
            {"field_name": "pay_period", "field_type": "text", "is_required": True, "display_order": 3, "description": "Payroll period (e.g. Oct 2026)"},
            {"field_name": "gross_salary", "field_type": "number", "is_required": True, "display_order": 4, "description": "Total gross salary"},
            {"field_name": "deductions", "field_type": "number", "is_required": False, "display_order": 5, "description": "Total deductions applied"},
            {"field_name": "net_salary", "field_type": "number", "is_required": True, "display_order": 6, "description": "Total net salary paid"},
        ],
        "line_items": [
            {"column_name": "component_name", "column_type": "text", "is_required": True, "display_order": 1},
            {"column_name": "component_amount", "column_type": "number", "is_required": True, "display_order": 2},
        ]
    },
]


async def seed_idp_templates(session: AsyncSession) -> None:
    """Seed predefined IDP global templates if they don't exist.

    This runs on system startup inside initialize_database context.
    """
    logger.debug("Starting IDP templates seeding...")
    seeded_count = 0
    for t_def in DEFAULT_TEMPLATES:
        # Check name uniqueness for global templates (where org_id is null)
        stmt = select(IdpFieldConfiguration).where(
            IdpFieldConfiguration.name == t_def["name"],
            IdpFieldConfiguration.org_id.is_(None),
            IdpFieldConfiguration.is_template == True,
            IdpFieldConfiguration.deleted_at.is_(None),
        )
        existing = (await session.exec(stmt)).first()
        if existing and existing.doc_type is None:
            existing.doc_type = t_def["name"]
            session.add(existing)
            seeded_count += 1
        if not existing:
            new_config = IdpFieldConfiguration(
                name=t_def["name"],
                description=t_def["description"],
                doc_type=t_def["name"],
                org_id=None,
                is_template=True,
                is_active=True,
                extra=None,
                created_by=None,
                updated_by=None,
            )
            # Create headers
            if "headers" in t_def:
                new_config.headers = [
                    IdpFieldConfigHeader(
                        field_name=h["field_name"],
                        field_type=h["field_type"],
                        is_required=h["is_required"],
                        display_order=h["display_order"],
                        description=h.get("description"),
                    )
                    for h in t_def["headers"]
                ]
            # Create line items
            if "line_items" in t_def:
                new_config.line_items = [
                    IdpFieldConfigLineItem(
                        column_name=l["column_name"],
                        column_type=l["column_type"],
                        is_required=l["is_required"],
                        display_order=l["display_order"],
                    )
                    for l in t_def["line_items"]
                ]
            session.add(new_config)
            seeded_count += 1

    if seeded_count > 0:
        await session.commit()
        logger.info(f"Seeded {seeded_count} new IDP templates in database.")
    else:
        logger.debug("All standard IDP templates are already seeded.")
