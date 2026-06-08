import json
import pytest
from typing import Any
from uuid import uuid4
from agentcore.services.idp.extraction import extract_dynamic
from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
from agentcore.schema.data import Data
from agentcore.schema.message import Message

@pytest.fixture
def anyio_backend():
    return "asyncio"

class MockResponse:
    def __init__(self, content: str):
        self.content = content

class MockLLM:
    """Mock LLM to simulate structured tool calls or standard responses."""
    def __init__(self, response_text: str, support_structured: bool = False, structured_result: Any = None):
        self.response_text = response_text
        self.support_structured = support_structured
        self.structured_result = structured_result
        self.invoked_messages = []

    async def ainvoke(self, messages, **kwargs):
        self.invoked_messages = messages
        return MockResponse(self.response_text)

    def with_structured_output(self, schema_cls):
        if self.support_structured:
            # Return self so ainvoke gets called and we can return structured_result
            return self
        raise AttributeError("Structured output not supported on this mock.")

@pytest.mark.anyio
async def test_extract_dynamic_success_structured():
    """Verify extract_dynamic works when the LLM supports structured outputs."""
    from agentcore.services.idp.extraction import StructuredExtractionResult
    
    mock_extracted = StructuredExtractionResult(
        headers={
            "invoice_number": {"value": "INV-1001", "confidence": 0.95, "reasoning": "Located at top-right"}
        },
        line_items=[
            {
                "row_index": 0,
                "columns": [
                    {"column_name": "description", "value": "Service fee", "confidence": 0.9, "reasoning": "Row 1 desc"},
                    {"column_name": "amount", "value": "150.00", "confidence": 0.92, "reasoning": "Row 1 amount"}
                ]
            }
        ]
    )

    mock_llm = MockLLM(
        response_text="", 
        support_structured=True
    )
    # Set the structured result that we want to get back when structured_model.ainvoke is called
    mock_llm.structured_result = mock_extracted
    
    # We patch structured result behavior
    async def mock_structured_ainvoke(messages, **kwargs):
        return mock_extracted
    mock_llm.ainvoke = mock_structured_ainvoke

    res = await extract_dynamic(
        ocr_text="Invoice INV-1001. Description: Service fee, Amount: 150.00",
        prompt="Extract invoice number and line items",
        llm_model=mock_llm
    )

    assert "headers" in res
    assert "line_items" in res
    assert res["headers"]["invoice_number"]["value"] == "INV-1001"
    assert res["headers"]["invoice_number"]["confidence"] == 0.95
    assert len(res["line_items"]) == 1
    assert res["line_items"][0]["columns"][0]["column_name"] == "description"
    assert res["line_items"][0]["columns"][0]["value"] == "Service fee"

@pytest.mark.anyio
async def test_extract_dynamic_success_raw_json():
    """Verify extract_dynamic works by parsing raw JSON when structured outputs are not supported."""
    raw_json_response = """
    ```json
    {
      "headers": {
        "invoice_number": {
          "value": "INV-1002",
          "confidence": 0.98,
          "reasoning": "Found near header"
        }
      },
      "line_items": [
        {
          "row_index": 0,
          "columns": [
            {
              "column_name": "description",
              "value": "License A",
              "confidence": 0.9,
              "reasoning": "Table row 1"
            }
          ]
        }
      ]
    }
    ```
    """
    mock_llm = MockLLM(response_text=raw_json_response)
    res = await extract_dynamic(
        ocr_text="Invoice INV-1002. Product: License A",
        prompt="Extract fields",
        llm_model=mock_llm
    )

    assert "headers" in res
    assert res["headers"]["invoice_number"]["value"] == "INV-1002"
    assert len(res["line_items"]) == 1
    assert res["line_items"][0]["columns"][0]["value"] == "License A"

@pytest.mark.anyio
async def test_extract_dynamic_dirty_json_fallback():
    """Verify that extract_dynamic handles non-conforming or flat JSON back from the model."""
    raw_dirty_json = """
    {
      "headers": {
        "invoice_number": "INV-1003"
      },
      "line_items": [
        {
          "row_index": 0,
          "columns": [
            {
              "column_name": "description",
              "value": "Setup fee"
            }
          ]
        }
      ]
    }
    """
    mock_llm = MockLLM(response_text=raw_dirty_json)
    res = await extract_dynamic(
        ocr_text="Invoice INV-1003",
        prompt="Extract fields",
        llm_model=mock_llm
    )

    assert "headers" in res
    # Flat value "INV-1003" should be converted to Structured format
    assert res["headers"]["invoice_number"]["value"] == "INV-1003"
    assert res["headers"]["invoice_number"]["confidence"] == 0.8
    assert res["headers"]["invoice_number"]["reasoning"] == "Direct extraction"
    
    # Missing columns confidence should default to 0.8
    assert res["line_items"][0]["columns"][0]["confidence"] == 0.8

@pytest.mark.anyio
async def test_llm_extractor_node_integration():
    """Verify the IDPLLMExtractor node calls the extract_dynamic method in dynamic_prompt mode."""
    raw_json_response = """
    {
      "headers": {
        "total": {
          "value": "500.00",
          "confidence": 0.99,
          "reasoning": "Reason here"
        }
      },
      "line_items": []
    }
    """
    mock_llm = MockLLM(response_text=raw_json_response)
    
    # Set up node inputs
    node = IDPLLMExtractor()
    node.document = Message(text="Total: 500.00")
    node.llm = mock_llm
    node.extraction_mode = "dynamic_prompt"
    node.prompt = "Extract total"

    # Call the node method
    out_data = await node.extract()
    
    assert isinstance(out_data, Data)
    assert out_data.data["headers"]["total"]["value"] == "500.00"
    assert "1 header(s)" in node.status
