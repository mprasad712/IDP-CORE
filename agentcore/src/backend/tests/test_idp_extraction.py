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


@pytest.mark.anyio
async def test_extract_named_config_success():
    """Verify extract_named_config fetches configuration, formats prompts, calls extract_dynamic, and filters outputs."""
    from agentcore.services.deps import session_scope
    from agentcore.services.database.models.idp.config import (
        IdpFieldConfiguration,
        IdpFieldConfigHeader,
        IdpFieldConfigLineItem,
    )
    from agentcore.services.idp.extraction import extract_named_config
    
    # 1. Create a config schema in DB
    config_id = uuid4()
    async with session_scope() as session:
        config = IdpFieldConfiguration(
            id=config_id,
            name="Extraction Test Config",
            is_active=True,
        )
        session.add(config)
        await session.flush()
        
        # Add a header field and a line item field
        header = IdpFieldConfigHeader(
            id=uuid4(),
            config_id=config_id,
            field_name="invoice_number",
            field_type="text",
            display_order=0,
        )
        line = IdpFieldConfigLineItem(
            id=uuid4(),
            config_id=config_id,
            column_name="price",
            column_type="number",
            display_order=0,
        )
        session.add(header)
        session.add(line)
        await session.commit()

    # 2. Mock LLM output containing both allowed fields and some extra hallucinated fields
    raw_response = {
        "headers": {
            "invoice_number": {
                "value": "INV-2001",
                "confidence": 0.96,
                "reasoning": "Top left"
            },
            "extra_hallucinated_header": {
                "value": "junk",
                "confidence": 0.5,
                "reasoning": "should be filtered"
            }
        },
        "line_items": [
            {
                "row_index": 0,
                "columns": [
                    {
                        "column_name": "price",
                        "value": "45.00",
                        "confidence": 0.9,
                        "reasoning": "Col 1"
                    },
                    {
                        "column_name": "qty",
                        "value": "2",
                        "confidence": 0.9,
                        "reasoning": "Should be filtered"
                    }
                ]
            }
        ]
    }
    
    mock_llm = MockLLM(response_text=json.dumps(raw_response))
    
    async with session_scope() as session:
        res = await extract_named_config(
            session=session,
            ocr_text="Invoice INV-2001. Price: 45.00, Qty: 2",
            field_config_id=config_id,
            llm_model=mock_llm
        )
        
    assert "headers" in res
    assert "line_items" in res
    
    # 3. Verify only configured fields are returned
    assert "invoice_number" in res["headers"]
    assert "extra_hallucinated_header" not in res["headers"]
    assert res["headers"]["invoice_number"]["value"] == "INV-2001"
    
    assert len(res["line_items"]) == 1
    cols = {c["column_name"]: c for c in res["line_items"][0]["columns"]}
    assert "price" in cols
    assert "qty" not in cols
    assert cols["price"]["value"] == "45.00"

    # 4. Clean up
    async with session_scope() as session:
        db_cfg = await session.get(IdpFieldConfiguration, config_id)
        if db_cfg:
            await session.delete(db_cfg)
        await session.commit()


@pytest.mark.anyio
async def test_llm_extractor_node_named_config_integration():
    """Verify IDPLLMExtractor node calls extract_named_config when mode is field_configuration."""
    from agentcore.services.deps import session_scope
    from agentcore.services.database.models.idp.config import (
        IdpFieldConfiguration,
        IdpFieldConfigHeader,
    )
    
    config_id = uuid4()
    async with session_scope() as session:
        config = IdpFieldConfiguration(
            id=config_id,
            name="Node Config Test",
            is_active=True,
        )
        session.add(config)
        await session.flush()
        
        header = IdpFieldConfigHeader(
            id=uuid4(),
            config_id=config_id,
            field_name="total",
            field_type="number",
            display_order=0,
        )
        session.add(header)
        await session.commit()

    raw_response = {
        "headers": {
            "total": {
                "value": "750.00",
                "confidence": 0.99,
                "reasoning": "Total text"
            }
        },
        "line_items": []
    }
    mock_llm = MockLLM(response_text=json.dumps(raw_response))
    
    node = IDPLLMExtractor()
    node.document = Message(text="Total sum is 750.00")
    node.llm = mock_llm
    node.extraction_mode = "field_configuration"
    node.config_name = "Node Config Test"
    
    out_data = await node.extract()
    
    assert isinstance(out_data, Data)
    assert out_data.data["headers"]["total"]["value"] == "750.00"
    
    # Cleanup
    async with session_scope() as session:
        db_cfg = await session.get(IdpFieldConfiguration, config_id)
        if db_cfg:
            await session.delete(db_cfg)
        await session.commit()
