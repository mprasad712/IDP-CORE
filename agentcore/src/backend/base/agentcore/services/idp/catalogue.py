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

# 32 standard document templates across multiple categories
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
        "name": "Receipt",
        "description": "Store receipts and proof of transaction slips.",
        "headers": [
            {"field_name": "merchant_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Name of the merchant/store"},
            {"field_name": "transaction_date", "field_type": "date", "is_required": True, "display_order": 2, "description": "Date of transaction"},
            {"field_name": "total_amount", "field_type": "number", "is_required": True, "display_order": 3, "description": "Total amount paid"},
            {"field_name": "payment_method", "field_type": "text", "is_required": False, "display_order": 4, "description": "Payment method used"},
        ],
        "line_items": [
            {"column_name": "item_name", "column_type": "text", "is_required": True, "display_order": 1},
            {"column_name": "item_amount", "column_type": "number", "is_required": True, "display_order": 2},
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
    {
        "name": "Delivery Note",
        "description": "Supplier dispatch note accompanying shipments.",
        "headers": [
            {"field_name": "delivery_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Delivery note unique number"},
            {"field_name": "delivery_date", "field_type": "date", "is_required": True, "display_order": 2, "description": "Date of delivery"},
            {"field_name": "sender_name", "field_type": "text", "is_required": True, "display_order": 3, "description": "Name of the sender"},
            {"field_name": "receiver_name", "field_type": "text", "is_required": True, "display_order": 4, "description": "Name of the receiver"},
        ],
        "line_items": [
            {"column_name": "description", "column_type": "text", "is_required": True, "display_order": 1},
            {"column_name": "qty_delivered", "column_type": "number", "is_required": True, "display_order": 2},
        ]
    },
    {
        "name": "Remittance Advice",
        "description": "Proof of payment details sent by customer to supplier.",
        "headers": [
            {"field_name": "remittance_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Remittance reference ID"},
            {"field_name": "payment_date", "field_type": "date", "is_required": True, "display_order": 2, "description": "Date payment was processed"},
            {"field_name": "payment_method", "field_type": "text", "is_required": False, "display_order": 3, "description": "Method of payment (e.g. ACH)"},
            {"field_name": "total_paid", "field_type": "number", "is_required": True, "display_order": 4, "description": "Total amount remitted"},
        ],
        "line_items": [
            {"column_name": "invoice_number", "column_type": "text", "is_required": True, "display_order": 1},
            {"column_name": "invoice_date", "column_type": "date", "is_required": False, "display_order": 2},
            {"column_name": "amount_paid", "column_type": "number", "is_required": True, "display_order": 3},
        ]
    },
    {
        "name": "Vendor Statement",
        "description": "Periodic statement of supplier account listing open items.",
        "headers": [
            {"field_name": "vendor_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Vendor name"},
            {"field_name": "statement_date", "field_type": "date", "is_required": True, "display_order": 2, "description": "Date of statement"},
            {"field_name": "account_number", "field_type": "text", "is_required": False, "display_order": 3, "description": "Vendor account ID"},
            {"field_name": "outstanding_balance", "field_type": "number", "is_required": True, "display_order": 4, "description": "Total outstanding balance"},
        ],
        "line_items": [
            {"column_name": "document_date", "column_type": "date", "is_required": True, "display_order": 1},
            {"column_name": "document_reference", "column_type": "text", "is_required": True, "display_order": 2},
            {"column_name": "amount", "column_type": "number", "is_required": True, "display_order": 3},
        ]
    },

    # --- Financial - Banking ---
    {
        "name": "Bank Statement",
        "description": "Standard retail bank account statement of transactions.",
        "headers": [
            {"field_name": "bank_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Bank name"},
            {"field_name": "account_number", "field_type": "text", "is_required": True, "display_order": 2, "description": "Account number"},
            {"field_name": "account_holder", "field_type": "text", "is_required": True, "display_order": 3, "description": "Account holder name"},
            {"field_name": "opening_balance", "field_type": "number", "is_required": True, "display_order": 4, "description": "Account opening balance"},
            {"field_name": "closing_balance", "field_type": "number", "is_required": True, "display_order": 5, "description": "Account closing balance"},
            {"field_name": "statement_period", "field_type": "text", "is_required": False, "display_order": 6, "description": "Period covered by statement"},
        ],
        "line_items": [
            {"column_name": "transaction_date", "column_type": "date", "is_required": True, "display_order": 1},
            {"column_name": "description", "column_type": "text", "is_required": True, "display_order": 2},
            {"column_name": "amount", "column_type": "number", "is_required": True, "display_order": 3},
            {"column_name": "transaction_type", "column_type": "text", "is_required": True, "display_order": 4},
        ]
    },
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
    {
        "name": "Wire Transfer Slip",
        "description": "Receipt or transaction slip confirming an electronic wire transfer.",
        "headers": [
            {"field_name": "transaction_reference", "field_type": "text", "is_required": True, "display_order": 1, "description": "Wire transfer reference"},
            {"field_name": "transfer_date", "field_type": "date", "is_required": True, "display_order": 2, "description": "Date of transfer"},
            {"field_name": "sender_account", "field_type": "text", "is_required": True, "display_order": 3, "description": "Sender account number"},
            {"field_name": "receiver_account", "field_type": "text", "is_required": True, "display_order": 4, "description": "Receiver account number"},
            {"field_name": "amount", "field_type": "number", "is_required": True, "display_order": 5, "description": "Transferred amount"},
        ],
        "line_items": []
    },
    {
        "name": "Loan Agreement",
        "description": "Loan documentation details including principal and interest rules.",
        "headers": [
            {"field_name": "lender_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Lender name"},
            {"field_name": "borrower_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Borrower name"},
            {"field_name": "principal_amount", "field_type": "number", "is_required": True, "display_order": 3, "description": "Principal loan amount"},
            {"field_name": "interest_rate", "field_type": "number", "is_required": True, "display_order": 4, "description": "Annual interest rate (e.g. 5.5)"},
            {"field_name": "agreement_date", "field_type": "date", "is_required": True, "display_order": 5, "description": "Agreement execution date"},
        ],
        "line_items": []
    },
    {
        "name": "Mortgage Document",
        "description": "Property mortgage registry and financial outlines.",
        "headers": [
            {"field_name": "mortgage_provider", "field_type": "text", "is_required": True, "display_order": 1, "description": "Mortgage provider"},
            {"field_name": "property_address", "field_type": "text", "is_required": True, "display_order": 2, "description": "Address of mortgaged property"},
            {"field_name": "mortgage_amount", "field_type": "number", "is_required": True, "display_order": 3, "description": "Approved mortgage value"},
            {"field_name": "term_months", "field_type": "number", "is_required": False, "display_order": 4, "description": "Total mortgage term in months"},
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
        "name": "National ID Card",
        "description": "Government citizens identity identification card.",
        "headers": [
            {"field_name": "national_id", "field_type": "text", "is_required": True, "display_order": 1, "description": "Citizenship/National ID reference"},
            {"field_name": "full_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Holder full name"},
            {"field_name": "date_of_birth", "field_type": "date", "is_required": True, "display_order": 3, "description": "Date of birth"},
            {"field_name": "expiry_date", "field_type": "date", "is_required": False, "display_order": 4, "description": "Card expiration date"},
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

    # --- Tax & Compliance ---
    {
        "name": "Tax Return",
        "description": "Filing tax returns summary schemas.",
        "headers": [
            {"field_name": "taxpayer_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Name of taxpayer"},
            {"field_name": "tax_id", "field_type": "text", "is_required": True, "display_order": 2, "description": "Tax Identification Number (TIN)"},
            {"field_name": "tax_year", "field_type": "text", "is_required": True, "display_order": 3, "description": "Financial year filed"},
            {"field_name": "gross_income", "field_type": "number", "is_required": True, "display_order": 4, "description": "Declared gross income"},
            {"field_name": "tax_payable", "field_type": "number", "is_required": True, "display_order": 5, "description": "Total tax payable/refundable"},
        ],
        "line_items": []
    },
    {
        "name": "Tax Certificate",
        "description": "Certificate proving tax registration, clearance or status.",
        "headers": [
            {"field_name": "certificate_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Tax certificate number"},
            {"field_name": "entity_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Entity name registered"},
            {"field_name": "tax_registration_number", "field_type": "text", "is_required": True, "display_order": 3, "description": "Tax Registration Number (TRN)"},
            {"field_name": "issue_date", "field_type": "date", "is_required": True, "display_order": 4, "description": "Date certificate was issued"},
        ],
        "line_items": []
    },
    {
        "name": "Withholding Certificate",
        "description": "Certificate proving tax withheld at source.",
        "headers": [
            {"field_name": "withholding_agent", "field_type": "text", "is_required": True, "display_order": 1, "description": "Entity withholding tax"},
            {"field_name": "recipient_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Recipient of withholding certificate"},
            {"field_name": "amount_paid", "field_type": "number", "is_required": True, "display_order": 3, "description": "Total amount paid before withholding"},
            {"field_name": "amount_withheld", "field_type": "number", "is_required": True, "display_order": 4, "description": "Total amount withheld"},
        ],
        "line_items": []
    },
    {
        "name": "Customs Declaration",
        "description": "Customs import or export entry declarations.",
        "headers": [
            {"field_name": "declaration_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Customs declaration ID"},
            {"field_name": "importer_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Importer name"},
            {"field_name": "exporter_name", "field_type": "text", "is_required": True, "display_order": 3, "description": "Exporter name"},
            {"field_name": "customs_value", "field_type": "number", "is_required": True, "display_order": 4, "description": "Total customs value declared"},
        ],
        "line_items": [
            {"column_name": "description", "column_type": "text", "is_required": True, "display_order": 1},
            {"column_name": "hs_code", "column_type": "text", "is_required": True, "display_order": 2},
        ]
    },
    {
        "name": "Tax Notice",
        "description": "Official notification from tax departments.",
        "headers": [
            {"field_name": "notice_reference", "field_type": "text", "is_required": True, "display_order": 1, "description": "Notice unique ID"},
            {"field_name": "taxpayer_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Taxpayer name"},
            {"field_name": "assessment_year", "field_type": "text", "is_required": False, "display_order": 3, "description": "Assessment period year"},
            {"field_name": "notice_date", "field_type": "date", "is_required": True, "display_order": 4, "description": "Notice issue date"},
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
    {
        "name": "NDA",
        "description": "Non-Disclosure Agreement mutual or unilateral configurations.",
        "headers": [
            {"field_name": "disclosing_party", "field_type": "text", "is_required": True, "display_order": 1, "description": "Party sharing confidential data"},
            {"field_name": "receiving_party", "field_type": "text", "is_required": True, "display_order": 2, "description": "Party receiving confidential data"},
            {"field_name": "effective_date", "field_type": "date", "is_required": True, "display_order": 3, "description": "NDA active date"},
            {"field_name": "term_years", "field_type": "number", "is_required": False, "display_order": 4, "description": "NDA term in years (e.g. 5)"},
        ],
        "line_items": []
    },
    {
        "name": "MSA",
        "description": "Master Services Agreement core terms configurations.",
        "headers": [
            {"field_name": "provider_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Service provider"},
            {"field_name": "client_name", "field_type": "text", "is_required": True, "display_order": 2, "description": "Client name"},
            {"field_name": "effective_date", "field_type": "date", "is_required": True, "display_order": 3, "description": "MSA effective date"},
            {"field_name": "governing_law", "field_type": "text", "is_required": False, "display_order": 4, "description": "Governing jurisdiction"},
        ],
        "line_items": []
    },
    {
        "name": "MoU",
        "description": "Memorandum of Understanding cooperation outlines.",
        "headers": [
            {"field_name": "partner_a", "field_type": "text", "is_required": True, "display_order": 1, "description": "First partner"},
            {"field_name": "partner_b", "field_type": "text", "is_required": True, "display_order": 2, "description": "Second partner"},
            {"field_name": "effective_date", "field_type": "date", "is_required": True, "display_order": 3, "description": "MoU execution date"},
        ],
        "line_items": []
    },
    {
        "name": "Lease Agreement",
        "description": "Residential or commercial property lease agreements.",
        "headers": [
            {"field_name": "landlord", "field_type": "text", "is_required": True, "display_order": 1, "description": "Property owner/landlord"},
            {"field_name": "tenant", "field_type": "text", "is_required": True, "display_order": 2, "description": "Renting tenant name"},
            {"field_name": "property_address", "field_type": "text", "is_required": True, "display_order": 3, "description": "Leased property address"},
            {"field_name": "monthly_rent", "field_type": "number", "is_required": True, "display_order": 4, "description": "Monthly rent amount"},
            {"field_name": "start_date", "field_type": "date", "is_required": True, "display_order": 5, "description": "Lease start date"},
            {"field_name": "end_date", "field_type": "date", "is_required": True, "display_order": 6, "description": "Lease end date"},
        ],
        "line_items": []
    },
    {
        "name": "Power of Attorney",
        "description": "Legal authorization documentation representing attorney powers.",
        "headers": [
            {"field_name": "principal", "field_type": "text", "is_required": True, "display_order": 1, "description": "Principal authorizing powers"},
            {"field_name": "agent_attorney", "field_type": "text", "is_required": True, "display_order": 2, "description": "Agent / Attorney-in-fact"},
            {"field_name": "execution_date", "field_type": "date", "is_required": True, "display_order": 3, "description": "Execution signing date"},
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

    # --- Insurance ---
    {
        "name": "Policy Document",
        "description": "Insurance policy scheme details outlining cover.",
        "headers": [
            {"field_name": "insurer_name", "field_type": "text", "is_required": True, "display_order": 1, "description": "Insurance company"},
            {"field_name": "policy_number", "field_type": "text", "is_required": True, "display_order": 2, "description": "Policy number"},
            {"field_name": "insured_name", "field_type": "text", "is_required": True, "display_order": 3, "description": "Primary insured name"},
            {"field_name": "premium_amount", "field_type": "number", "is_required": True, "display_order": 4, "description": "Premium cost"},
            {"field_name": "start_date", "field_type": "date", "is_required": True, "display_order": 5, "description": "Coverage start date"},
            {"field_name": "end_date", "field_type": "date", "is_required": True, "display_order": 6, "description": "Coverage end date"},
        ],
        "line_items": []
    },
    {
        "name": "Claim Form",
        "description": "Insurance claim request details.",
        "headers": [
            {"field_name": "claim_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Claim reference number"},
            {"field_name": "policy_number", "field_type": "text", "is_required": True, "display_order": 2, "description": "Policy number"},
            {"field_name": "claimant_name", "field_type": "text", "is_required": True, "display_order": 3, "description": "Claimant full name"},
            {"field_name": "claim_date", "field_type": "date", "is_required": True, "display_order": 4, "description": "Date of claim filing"},
            {"field_name": "claim_amount", "field_type": "number", "is_required": True, "display_order": 5, "description": "Claim amount requested"},
        ],
        "line_items": []
    },

    # --- Trade & Logistics ---
    {
        "name": "Bill of Lading",
        "description": "Carrier receipt and shipping terms document.",
        "headers": [
            {"field_name": "bol_number", "field_type": "text", "is_required": True, "display_order": 1, "description": "Bill of Lading identifier"},
            {"field_name": "shipper", "field_type": "text", "is_required": True, "display_order": 2, "description": "Shipper/Consignor name"},
            {"field_name": "consignee", "field_type": "text", "is_required": True, "display_order": 3, "description": "Consignee/Receiver name"},
            {"field_name": "port_of_loading", "field_type": "text", "is_required": False, "display_order": 4, "description": "Port of loading departure"},
            {"field_name": "port_of_discharge", "field_type": "text", "is_required": False, "display_order": 5, "description": "Port of discharge arrival"},
        ],
        "line_items": [
            {"column_name": "container_number", "column_type": "text", "is_required": True, "display_order": 1},
            {"column_name": "description", "column_type": "text", "is_required": True, "display_order": 2},
            {"column_name": "weight_kg", "column_type": "number", "is_required": False, "display_order": 3},
        ]
    }
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
