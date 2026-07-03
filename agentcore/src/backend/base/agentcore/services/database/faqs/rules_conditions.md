# How do I configure Rules / Conditions in the IDP Flow Canvas?

You can define custom routing rules using the 'Rules / Conditions' node in the IDP Flow Canvas. These rules determine whether a processed document is automatically approved or sent to pending review.

1. Anatomy of a Condition:
Each condition in the JSON array expects the following structure:
  - field: The name of the header field or table column to check (e.g., 'invoice_value', 'supplier_name', 'amount'). Use 'confidence' to check the overall document extraction confidence.
  - op: The operator defining the check type.
  - value: The target comparison value.

2. Supported Operators:
  - Numeric: 'gt' (>), 'lt' (<), 'gte' (>=), 'lte' (<=), 'eq' (==), 'neq' (!=)
  - Text: 'eq' (exact match), 'neq' (does not match), 'contains', 'starts_with', 'ends_with'
  - Date: 'gt', 'lt', 'gte', 'lte', 'eq', 'neq' (parsed dynamically)
  - Presence: 'is_present', 'is_missing' (checks if a field was extracted or not)
  - Visual Elements: Check 'signature', 'checkbox', 'qr', 'barcode' presence or checkbox state using 'is_present' or '==' (e.g. 'checked').

3. Logic Operator (AND vs. OR):
  - AND: All condition rows must evaluate to True. If even one fails, the document goes to Pending Review.
  - OR: If at least one condition evaluates to True, the document is approved. If all fail, it goes to Pending Review.

4. Hands-On JSON Examples:
  * Example 1: High Confidence & Amount Threshold (AND)
    Auto-approve only if the overall confidence is 85%+ and the amount is positive:
    [{"field":"confidence","op":"gte","value":0.85}, {"field":"amount","op":"gt","value":0}]

  * Example 2: Required PO Presence for Auto-Approval (AND)
    Ensure that a Purchase Order (PO) number was successfully extracted, and the invoice has a positive total:
    [{"field":"po_number","op":"is_present"}, {"field":"invoice_value","op":"gt","value":0}]

  * Example 3: Filter by Currency or Amount Limit (AND)
    Approve documents under $5,000 USD only:
    [{"field":"currency","op":"eq","value":"USD"}, {"field":"invoice_value","op":"lt","value":5000}]

  * Example 4: Visual Signature Check (OR)
    Approve if a signature is detected on the page OR if the checkbox is explicitly checked:
    [{"field":"signature","op":"is_present"}, {"field":"checkbox","op":"eq","value":"checked"}]
