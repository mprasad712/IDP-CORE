import pytest
from uuid import uuid4
from datetime import datetime, timezone

from agentcore.services.idp.rules_engine import evaluate_rules


class MockExtractedHeader:
    def __init__(self, field_name, value, confidence=0.9):
        self.field_name = field_name
        self.extracted_value = value
        self.confidence_score = confidence


class MockExtractedLineItem:
    def __init__(self, column_name, value, confidence=0.9, row_index=0):
        self.column_name = column_name
        self.extracted_value = value
        self.confidence_score = confidence
        self.row_index = row_index


class MockDetectedElement:
    def __init__(self, element_type, decoded_value=None, page=1):
        self.element_type = element_type
        self.decoded_value = decoded_value
        self.page_number = page


class MockRule:
    def __init__(
        self,
        rule_group=1,
        condition_type="confidence_overall",
        field_name=None,
        operator="==",
        value=None,
        field_b=None,
        pattern=None,
        action="auto_approve",
        display_order=1
    ):
        self.id = uuid4()
        self.rule_group = rule_group
        self.condition_type = condition_type
        self.field_name = field_name
        self.operator = operator
        self.value = value
        self.field_b = field_b
        self.pattern = pattern
        self.action = action
        self.display_order = display_order


def test_rules_engine_overall_confidence():
    rule = MockRule(condition_type="confidence_overall", operator=">=", value="0.80", action="auto_approve")
    
    # Pass case
    res = evaluate_rules(0.85, [], [], [], [rule])
    assert res["action"] == "auto_approve"
    assert res["matched_group"] == 1
    
    # Fail case
    res = evaluate_rules(0.75, [], [], [], [rule])
    assert res["action"] == "pending_review"
    assert len(res["failed_conditions"]) == 1


def test_rules_engine_field_value_numeric():
    headers = [MockExtractedHeader("total_amount", "1250.50")]
    rule = MockRule(condition_type="field_value_numeric", field_name="total_amount", operator=">", value="1000", action="auto_approve")
    
    res = evaluate_rules(0.9, headers, [], [], [rule])
    assert res["action"] == "auto_approve"
    
    # Fail case (below threshold)
    rule_fail = MockRule(condition_type="field_value_numeric", field_name="total_amount", operator=">", value="2000", action="auto_approve")
    res_fail = evaluate_rules(0.9, headers, [], [], [rule_fail])
    assert res_fail["action"] == "pending_review"


def test_rules_engine_field_value_text():
    headers = [MockExtractedHeader("vendor_name", "PwC India Private Limited")]
    rule = MockRule(condition_type="field_value_text", field_name="vendor_name", operator="contains", value="pwc", action="auto_approve")
    
    res = evaluate_rules(0.9, headers, [], [], [rule])
    assert res["action"] == "auto_approve"


def test_rules_engine_field_value_date():
    headers = [MockExtractedHeader("invoice_date", "2026-06-10")]
    rule = MockRule(condition_type="field_value_date", field_name="invoice_date", operator=">=", value="2026-06-01", action="auto_approve")
    
    res = evaluate_rules(0.9, headers, [], [], [rule])
    assert res["action"] == "auto_approve"


def test_rules_engine_field_comparison():
    headers = [
        MockExtractedHeader("subtotal", "1000"),
        MockExtractedHeader("total", "1200")
    ]
    rule = MockRule(condition_type="field_comparison", field_name="total", operator=">", field_b="subtotal", action="auto_approve")
    
    res = evaluate_rules(0.9, headers, [], [], [rule])
    assert res["action"] == "auto_approve"


def test_rules_engine_field_presence():
    headers = [MockExtractedHeader("po_number", "")]
    rule_missing = MockRule(condition_type="field_presence", field_name="po_number", operator="is_missing", action="auto_approve")
    res = evaluate_rules(0.9, headers, [], [], [rule_missing])
    assert res["action"] == "auto_approve"

    rule_present = MockRule(condition_type="field_presence", field_name="po_number", operator="is_present", action="auto_approve")
    res_fail = evaluate_rules(0.9, headers, [], [], [rule_present])
    assert res_fail["action"] == "pending_review"


def test_rules_engine_pattern_regex():
    headers = [MockExtractedHeader("invoice_no", "INV-2026-889")]
    rule = MockRule(condition_type="pattern_regex", field_name="invoice_no", pattern=r"^INV-\d{4}-\d+$", action="auto_approve")
    
    res = evaluate_rules(0.9, headers, [], [], [rule])
    assert res["action"] == "auto_approve"


def test_rules_engine_visual_element():
    elements = [
        MockDetectedElement("signature", page=1),
        MockDetectedElement("checkbox", decoded_value="checked", page=1)
    ]
    
    rule_sig = MockRule(condition_type="visual_element", field_name="signature", operator="is_present", action="auto_approve")
    res = evaluate_rules(0.9, [], [], elements, [rule_sig])
    assert res["action"] == "auto_approve"

    rule_cb = MockRule(condition_type="visual_element", field_name="checkbox", operator="==", value="checked", action="auto_approve")
    res2 = evaluate_rules(0.9, [], [], elements, [rule_cb])
    assert res2["action"] == "auto_approve"


def test_rules_engine_logical_and_and_first_match():
    # Rule group 1: overall_conf >= 0.85 AND total_amount < 5000
    rule1_a = MockRule(rule_group=1, condition_type="confidence_overall", operator=">=", value="0.85", action="auto_approve")
    rule1_b = MockRule(rule_group=1, condition_type="field_value_numeric", field_name="total_amount", operator="<", value="5000", action="auto_approve")
    
    # Rule group 2: signature is present
    rule2_a = MockRule(rule_group=2, condition_type="visual_element", field_name="signature", operator="is_present", action="pending_review")
    
    rules = [rule1_a, rule1_b, rule2_a]
    
    # Case: Group 1 matches (overrides Group 2's action due to first-match priority)
    headers = [MockExtractedHeader("total_amount", "1200")]
    elements = [MockDetectedElement("signature")]
    
    res = evaluate_rules(0.9, headers, [], elements, rules)
    assert res["action"] == "auto_approve"
    assert res["matched_group"] == 1

    # Case: Group 1 fails (due to total_amount >= 5000), Group 2 matches (returns pending_review)
    headers_large = [MockExtractedHeader("total_amount", "6000")]
    res2 = evaluate_rules(0.9, headers_large, [], elements, rules)
    assert res2["action"] == "pending_review"
    assert res2["matched_group"] == 2
