import pytest
from agentcore.services.idp.math_reconcile import parse_float, reconcile_math
from agentcore.services.idp.extraction import StructuredExtractionResult


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_parse_float():
    # Standard numbers
    assert parse_float(12.34) == 12.34
    assert parse_float("12.34") == 12.34
    assert parse_float(100) == 100.0

    # Currency and space styling
    assert parse_float("$120.50") == 120.50
    assert parse_float("£ 1,234.56") == 1234.56
    assert parse_float("1,000") == 1000.0

    # European / other locale comma styles
    assert parse_float("1.234,56") == 1234.56
    assert parse_float("12,34") == 12.34

    # Edge cases
    assert parse_float(None) is None
    assert parse_float("") is None
    assert parse_float("unknown") is None


class MockLLM:
    def __init__(self, structured_result):
        self.structured_result = structured_result
        self.invoked = False

    def with_structured_output(self, schema_cls):
        return self

    async def ainvoke(self, messages, **kwargs):
        self.invoked = True
        return self.structured_result


@pytest.mark.anyio
async def test_reconcile_math_balanced():
    # Initial balanced document
    extracted = {
        "headers": {
            "total_amount": {"value": "150.00", "confidence": 0.9}
        },
        "line_items": [
            {
                "row_index": 0,
                "columns": [
                    {"column_name": "amount", "value": "100.00", "confidence": 0.9},
                    {"column_name": "description", "value": "item 1", "confidence": 0.9}
                ]
            },
            {
                "row_index": 1,
                "columns": [
                    {"column_name": "amount", "value": "50.00", "confidence": 0.9},
                    {"column_name": "description", "value": "item 2", "confidence": 0.9}
                ]
            }
        ]
    }
    
    mock_llm = MockLLM(None)
    res = await reconcile_math(extracted, mock_llm)
    
    assert res["balanced"] is True
    assert res["attempts"] == 0
    assert not mock_llm.invoked
    assert "Initial extraction is mathematically balanced." in res["report"][0]


@pytest.mark.anyio
async def test_reconcile_math_unbalanced_resolved():
    # Unbalanced document: total is 150.00, sum of lines is 100.00 (diff = 50.00)
    unbalanced_extracted = {
        "headers": {
            "total_amount": {"value": "150.00", "confidence": 0.9}
        },
        "line_items": [
            {
                "row_index": 0,
                "columns": [
                    {"column_name": "amount", "value": "50.00", "confidence": 0.9}
                ]
            },
            {
                "row_index": 1,
                "columns": [
                    {"column_name": "amount", "value": "50.00", "confidence": 0.9}
                ]
            }
        ]
    }

    # Correction fixes the total to 100.00
    corrected_result = StructuredExtractionResult.model_validate({
        "headers": {
            "total_amount": {"value": "100.00", "confidence": 0.95}
        },
        "line_items": [
            {
                "row_index": 0,
                "columns": [
                    {"column_name": "amount", "value": "50.00", "confidence": 0.9}
                ]
            },
            {
                "row_index": 1,
                "columns": [
                    {"column_name": "amount", "value": "50.00", "confidence": 0.9}
                ]
            }
        ]
    })

    mock_llm = MockLLM(corrected_result)
    res = await reconcile_math(unbalanced_extracted, mock_llm)

    assert res["balanced"] is True
    assert res["attempts"] == 1
    assert mock_llm.invoked
    assert res["extracted"]["headers"]["total_amount"]["value"] == "100.00"
    assert "Balanced successfully" in res["report"][-1]


@pytest.mark.anyio
async def test_reconcile_math_unbalanced_unresolved():
    unbalanced_extracted = {
        "headers": {
            "total_amount": {"value": "150.00", "confidence": 0.9}
        },
        "line_items": [
            {
                "row_index": 0,
                "columns": [
                    {"column_name": "amount", "value": "50.00", "confidence": 0.9}
                ]
            }
        ]
    }

    # Mock LLM is called but returns still unbalanced result
    incorrect_correction = StructuredExtractionResult.model_validate({
        "headers": {
            "total_amount": {"value": "150.00", "confidence": 0.9}
        },
        "line_items": [
            {
                "row_index": 0,
                "columns": [
                    {"column_name": "amount", "value": "50.00", "confidence": 0.9}
                ]
            }
        ]
    })

    mock_llm = MockLLM(incorrect_correction)
    res = await reconcile_math(unbalanced_extracted, mock_llm, max_attempts=2)

    assert res["balanced"] is False
    assert res["attempts"] == 2
    assert mock_llm.invoked
    assert "failed to reconcile" in res["report"][-1]
