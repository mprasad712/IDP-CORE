import asyncio
from sqlmodel import select
from agentcore.services.deps import session_scope
from agentcore.services.database.models.help_support.model import HelpSupportQuestion

async def main():
    question_text = "How do I configure Rules / Conditions in the IDP Flow Canvas?"
    answer_text = (
        "You can define custom routing rules using the 'Rules / Conditions' node in the IDP Flow Canvas. "
        "These rules determine whether a processed document is automatically approved or sent to pending review.\n\n"
        "1. Anatomy of a Condition:\n"
        "Each condition in the JSON array expects the following structure:\n"
        "  - field: The name of the header field or table column to check (e.g., 'invoice_value', 'supplier_name', 'amount'). Use 'confidence' to check the overall document extraction confidence.\n"
        "  - op: The operator defining the check type.\n"
        "  - value: The target comparison value.\n\n"
        "2. Supported Operators:\n"
        "  - Numeric: 'gt' (>), 'lt' (<), 'gte' (>=), 'lte' (<=), 'eq' (==), 'neq' (!=)\n"
        "  - Text: 'eq' (exact match), 'neq' (does not match), 'contains', 'starts_with', 'ends_with'\n"
        "  - Date: 'gt', 'lt', 'gte', 'lte', 'eq', 'neq' (parsed dynamically)\n"
        "  - Presence: 'is_present', 'is_missing' (checks if a field was extracted or not)\n"
        "  - Visual Elements: Check 'signature', 'checkbox', 'qr', 'barcode' presence or checkbox state using 'is_present' or '==' (e.g. 'checked').\n\n"
        "3. Logic Operator (AND vs. OR):\n"
        "  - AND: All condition rows must evaluate to True. If even one fails, the document goes to Pending Review.\n"
        "  - OR: If at least one condition evaluates to True, the document is approved. If all fail, it goes to Pending Review.\n\n"
        "4. Hands-On JSON Examples:\n"
        "  * Example 1: High Confidence & Amount Threshold (AND)\n"
        "    Auto-approve only if the overall confidence is 85%+ and the amount is positive:\n"
        "    [{\"field\":\"confidence\",\"op\":\"gte\",\"value\":0.85}, {\"field\":\"amount\",\"op\":\"gt\",\"value\":0}]\n\n"
        "  * Example 2: Required PO Presence for Auto-Approval (AND)\n"
        "    Ensure that a Purchase Order (PO) number was successfully extracted, and the invoice has a positive total:\n"
        "    [{\"field\":\"po_number\",\"op\":\"is_present\"}, {\"field\":\"invoice_value\",\"op\":\"gt\",\"value\":0}]\n\n"
        "  * Example 3: Filter by Currency or Amount Limit (AND)\n"
        "    Approve documents under $5,000 USD only:\n"
        "    [{\"field\":\"currency\",\"op\":\"eq\",\"value\":\"USD\"}, {\"field\":\"invoice_value\",\"op\":\"lt\",\"value\":5000}]\n\n"
        "  * Example 4: Visual Signature Check (OR)\n"
        "    Approve if a signature is detected on the page OR if the checkbox is explicitly checked:\n"
        "    [{\"field\":\"signature\",\"op\":\"is_present\"}, {\"field\":\"checkbox\",\"op\":\"eq\",\"value\":\"checked\"}]"
    )

    async with session_scope() as session:
        existing = (await session.exec(select(HelpSupportQuestion).where(HelpSupportQuestion.question == question_text))).first()
        if existing:
            print("FAQ entry exists! Updating the answer...")
            existing.answer = answer_text
            session.add(existing)
        else:
            print("Creating new FAQ entry...")
            new_faq = HelpSupportQuestion(question=question_text, answer=answer_text)
            session.add(new_faq)
        await session.commit()
        print("FAQ entry updated successfully!")

if __name__ == "__main__":
    asyncio.run(main())
