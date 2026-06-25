from __future__ import annotations

import re
from datetime import datetime
from loguru import logger

try:
    import pandas as pd
except ImportError:
    pd = None

# Real-world money/number strings ("$1,200.00", "₹ 19.80", "12.5%", "USD 1,000") must coerce to a
# float for numeric rule comparisons, while genuine non-numbers ("INV-123", "N/A") must be REJECTED
# (so a numeric rule on an identifier-like value fails cleanly instead of comparing a wrong number).
# Strategy: remove ONLY known currency tokens / separators / % / whitespace, then require the
# remainder to be a pure number. (Comma is treated as a thousands separator — the dominant US/Indian
# convention; European decimal-comma like "1.200,00" is NOT supported and will mis-parse.)
_CURRENCY_WORDS = re.compile(r"\b(USD|EUR|GBP|INR|RS|JPY|CAD|AUD|AED)\b", re.IGNORECASE)
_CURRENCY_SYMBOLS = "$€£₹¥"
_PURE_NUMBER = re.compile(r"-?\d+(\.\d+)?$")


def _to_number(v) -> float:
    """Coerce a raw extracted value to float, tolerating currency symbols/codes, thousands
    separators, percent signs and surrounding whitespace. Raises ValueError if, after removing
    those, the value is not a pure number (e.g. 'INV-123', 'N/A', '' all raise)."""
    if v is None or isinstance(v, bool):
        raise ValueError(f"not numeric: {v!r}")
    if isinstance(v, (int, float)):
        return float(v)
    s = _CURRENCY_WORDS.sub("", str(v))
    for ch in _CURRENCY_SYMBOLS + "%,":
        s = s.replace(ch, "")
    s = s.replace(" ", "").strip()
    if not _PURE_NUMBER.fullmatch(s):
        raise ValueError(f"not numeric: {v!r}")
    return float(s)


# Helper to parse dates dynamically
def _parse_date(val: str | None) -> datetime | None:
    if not val:
        return None
    val_str = str(val).strip()
    try:
        # Try isoformat first
        return datetime.fromisoformat(val_str.replace("Z", "+00:00"))
    except ValueError:
        pass
    if pd is not None:
        try:
            ts = pd.to_datetime(val_str, errors="raise")
            return ts.to_pydatetime()
        except Exception:
            pass
    # Basic fallbacks
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(val_str, fmt)
        except ValueError:
            continue
    return None


def _compare_values(val_a: any, val_b: any, operator: str, val_type: str = "text") -> bool:
    """Compare two values based on type and operator."""
    if val_a is None or val_b is None:
        if operator == "==":
            return val_a == val_b
        if operator == "!=":
            return val_a != val_b
        return False

    # Normalize type comparisons
    if val_type == "numeric":
        try:
            num_a = _to_number(val_a)
            num_b = _to_number(val_b)
            if operator == "==": return num_a == num_b
            if operator == "!=": return num_a != num_b
            if operator == ">": return num_a > num_b
            if operator == "<": return num_a < num_b
            if operator == ">=": return num_a >= num_b
            if operator == "<=": return num_a <= num_b
        except (ValueError, TypeError):
            return False
    elif val_type == "date":
        date_a = _parse_date(str(val_a))
        date_b = _parse_date(str(val_b))
        if not date_a or not date_b:
            return False
        if operator == "==": return date_a == date_b
        if operator == "!=": return date_a != date_b
        if operator == ">": return date_a > date_b
        if operator == "<": return date_a < date_b
        if operator == ">=": return date_a >= date_b
        if operator == "<=": return date_a <= date_b
    else:  # text
        str_a = str(val_a).strip().lower()
        str_b = str(val_b).strip().lower()
        if operator == "==": return str_a == str_b
        if operator == "!=": return str_a != str_b
        if operator == "contains": return str_b in str_a
        if operator == "starts_with": return str_a.startswith(str_b)
        if operator == "ends_with": return str_a.endswith(str_b)

    return False


def _evaluate_single_rule(
    rule: any,
    overall_confidence: float | None,
    headers: list[any],
    line_items: list[any],
    detected_elements: list[any],
    match_result: str | None = None,
) -> bool:
    """Evaluate a single rule condition."""
    cond = rule.condition_type

    # match_result — SAP two-way/three-way match outcome
    if cond == "match_result":
        if match_result is None:
            return False
        expected = str(rule.value).strip().lower()
        actual = match_result.strip().lower()
        if rule.operator in ("==", "equals", "eq"):
            return actual == expected
        if rule.operator in ("!=", "not_equals", "neq"):
            return actual != expected
        if rule.operator == "in":
            values = [v.strip().lower() for v in str(rule.value).split(",")]
            return actual in values
        return actual == expected

    # 1. confidence_overall
    if cond == "confidence_overall":
        if overall_confidence is None:
            return False
        return _compare_values(overall_confidence, rule.value, rule.operator, "numeric")

    # Resolve field values for fields-based checks
    field_name = rule.field_name
    header_val = None
    header_conf = None
    is_header = False

    for h in headers:
        if h.field_name == field_name:
            header_val = h.extracted_value
            header_conf = h.confidence_score
            is_header = True
            break

    line_vals = [li.extracted_value for li in line_items if li.column_name == field_name]
    line_confs = [li.confidence_score for li in line_items if li.column_name == field_name]
    is_line_item = len(line_vals) > 0

    # 2. confidence_field
    if cond == "confidence_field":
        if is_header:
            if header_conf is None:
                return False
            return _compare_values(header_conf, rule.value, rule.operator, "numeric")
        elif is_line_item:
            # All lines must satisfy the condition (strict validation)
            if not line_confs:
                return False
            return all(_compare_values(c, rule.value, rule.operator, "numeric") for c in line_confs if c is not None)
        return False

    # 3. field_value_numeric
    if cond == "field_value_numeric":
        if is_header:
            return _compare_values(header_val, rule.value, rule.operator, "numeric")
        elif is_line_item:
            if not line_vals:
                return False
            return all(_compare_values(v, rule.value, rule.operator, "numeric") for v in line_vals)
        return False

    # 4. field_value_text
    if cond == "field_value_text":
        if is_header:
            return _compare_values(header_val, rule.value, rule.operator, "text")
        elif is_line_item:
            if not line_vals:
                return False
            return all(_compare_values(v, rule.value, rule.operator, "text") for v in line_vals)
        return False

    # 5. field_value_date
    if cond == "field_value_date":
        if is_header:
            return _compare_values(header_val, rule.value, rule.operator, "date")
        elif is_line_item:
            if not line_vals:
                return False
            return all(_compare_values(v, rule.value, rule.operator, "date") for v in line_vals)
        return False

    # 6. field_comparison
    if cond == "field_comparison":
        # Lookup Field B
        field_b = rule.field_b
        header_val_b = next((h.extracted_value for h in headers if h.field_name == field_b), None)
        # Determine value type dynamically (try numeric, then date, fallback to text)
        val_type = "text"
        try:
            _to_number(header_val)
            _to_number(header_val_b)
            val_type = "numeric"
        except (ValueError, TypeError):
            if _parse_date(header_val) and _parse_date(header_val_b):
                val_type = "date"

        return _compare_values(header_val, header_val_b, rule.operator, val_type)

    # 7. field_presence
    if cond == "field_presence":
        val_to_check = header_val if is_header else (line_vals[0] if is_line_item else None)
        has_val = val_to_check is not None and str(val_to_check).strip() != ""
        if rule.operator == "is_present":
            return has_val
        if rule.operator == "is_missing":
            return not has_val
        return False

    # 8. pattern_regex
    if cond == "pattern_regex":
        val_to_check = header_val if is_header else (line_vals[0] if is_line_item else None)
        if val_to_check is None or not rule.pattern:
            return False
        try:
            pattern = re.compile(rule.pattern)
            return bool(pattern.search(str(val_to_check)))
        except re.error:
            logger.warning(f"Invalid regex pattern in rule: {rule.pattern}")
            return False

    # 9. visual_element
    if cond == "visual_element":
        element_type = rule.field_name # e.g. signature|stamp|checkbox
        matching_elements = [el for el in detected_elements if el.element_type == element_type]
        if rule.operator == "is_present":
            return len(matching_elements) > 0
        if rule.operator == "is_missing":
            return len(matching_elements) == 0
        if rule.operator == "==":
            if not matching_elements:
                return False
            # Check if any matches the decoded value (e.g. checkbox state)
            return any(str(el.decoded_value).strip().lower() == str(rule.value).strip().lower() for el in matching_elements)

    return False


def evaluate_rules(
    overall_confidence: float | None,
    headers: list[any],
    line_items: list[any],
    detected_elements: list[any],
    rules: list[any],
    default_action: str = "pending_review",
    match_result: str | None = None,
) -> dict:
    """Evaluate IDP Agent Rules grouped by rule_group.

    Each group is evaluated as logical AND (all rules must pass).
    First matching group wins (first-match action).
    If no group matches, default_action is returned.
    """
    if not rules:
        return {
            "action": default_action,
            "matched_group": None,
            "failed_conditions": []
        }

    # Group rules by rule_group
    groups = {}
    for r in rules:
        groups.setdefault(r.rule_group, []).append(r)

    failed_conditions = []
    
    # Process groups in order
    for group_id in sorted(groups.keys()):
        group_rules = groups[group_id]
        group_passed = True
        
        for rule in group_rules:
            passed = _evaluate_single_rule(rule, overall_confidence, headers, line_items, detected_elements, match_result=match_result)
            if not passed:
                group_passed = False
                failed_conditions.append({
                    "rule_id": str(rule.id),
                    "rule_group": rule.rule_group,
                    "condition_type": rule.condition_type,
                    "field_name": rule.field_name,
                    "operator": rule.operator
                })
                # In an AND group, once one rule fails, the group fails
                break

        if group_passed:
            # First match wins
            matched_action = group_rules[0].action
            return {
                "action": matched_action,
                "matched_group": group_id,
                "failed_conditions": []
            }

    return {
        "action": default_action,
        "matched_group": None,
        "failed_conditions": failed_conditions
    }
