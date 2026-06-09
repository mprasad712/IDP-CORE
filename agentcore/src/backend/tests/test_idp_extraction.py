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


@pytest.mark.anyio
async def test_extract_multimodal_success_prompt(tmp_path):
    """Verify extract_multimodal works with a raw prompt and image input."""
    from PIL import Image as PILImage
    from agentcore.services.idp.extraction import extract_multimodal

    # Create a simple image file
    img_path = tmp_path / "test.png"
    img = PILImage.new("RGB", (100, 100), color="white")
    img.save(img_path)

    raw_response = {
        "headers": {
            "invoice_number": {
                "value": "INV-M1",
                "confidence": 0.9,
                "reasoning": "Detected from vision prompt"
            }
        },
        "line_items": []
    }
    mock_llm = MockLLM(response_text=json.dumps(raw_response))

    res = await extract_multimodal(
        file_path=img_path,
        prompt_or_config_id="Extract invoice number",
        llm_model=mock_llm
    )

    assert "headers" in res
    assert res["headers"]["invoice_number"]["value"] == "INV-M1"
    # Verify the message content sent to the model is multimodal
    invoked = mock_llm.invoked_messages
    assert len(invoked) == 2
    human_msg = invoked[1]
    assert isinstance(human_msg.content, list)
    assert human_msg.content[0]["type"] == "text"
    assert human_msg.content[1]["type"] == "image_url"
    assert human_msg.content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.anyio
async def test_extract_multimodal_success_config(tmp_path):
    """Verify extract_multimodal works with a config schema and PDF input."""
    from reportlab.pdfgen import canvas
    from agentcore.services.deps import session_scope
    from agentcore.services.database.models.idp.config import (
        IdpFieldConfiguration,
        IdpFieldConfigHeader,
    )
    from agentcore.services.idp.extraction import extract_multimodal

    # Create a simple PDF file
    pdf_path = tmp_path / "test.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.drawString(100, 100, "Multimodal config PDF content")
    c.showPage()
    c.save()

    config_id = uuid4()
    async with session_scope() as session:
        config = IdpFieldConfiguration(
            id=config_id,
            name="Multimodal Config Test",
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
                "value": "99.99",
                "confidence": 0.95,
                "reasoning": "Reason here"
            },
            "unconfigured": {
                "value": "should be filtered",
                "confidence": 0.1,
                "reasoning": "filtered"
            }
        },
        "line_items": []
    }
    mock_llm = MockLLM(response_text=json.dumps(raw_response))

    async with session_scope() as session:
        res = await extract_multimodal(
            file_path=pdf_path,
            prompt_or_config_id=config_id,
            llm_model=mock_llm,
            session=session
        )

    assert "headers" in res
    assert "total" in res["headers"]
    assert "unconfigured" not in res["headers"]
    assert res["headers"]["total"]["value"] == "99.99"

    # Cleanup
    async with session_scope() as session:
        db_cfg = await session.get(IdpFieldConfiguration, config_id)
        if db_cfg:
            await session.delete(db_cfg)
        await session.commit()


@pytest.mark.anyio
async def test_llm_extractor_node_multimodal_integration(tmp_path):
    """Verify IDPLLMExtractor runs multimodal mode when selected."""
    from PIL import Image as PILImage
    
    img_path = tmp_path / "node_test.png"
    img = PILImage.new("RGB", (100, 100), color="white")
    img.save(img_path)

    raw_response = {
        "headers": {
            "invoice_number": {
                "value": "INV-NODE-M1",
                "confidence": 0.9,
                "reasoning": "Detected"
            }
        },
        "line_items": []
    }
    mock_llm = MockLLM(response_text=json.dumps(raw_response))

    node = IDPLLMExtractor()
    # Pass path inside Message files
    node.document = Message(text="Dummy text", files=[str(img_path)])
    node.llm = mock_llm
    node.extraction_mode = "multimodal_prompt"
    node.prompt = "Extract invoice"

    out_data = await node.extract()

    assert isinstance(out_data, Data)
    assert out_data.data["headers"]["invoice_number"]["value"] == "INV-NODE-M1"


@pytest.mark.anyio
async def test_save_extraction_results_success():
    """Verify that save_extraction_results computes confidence and maps source locations correctly."""
    from agentcore.services.deps import session_scope
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.database.models.idp.documents import (
        IdpDocument,
        IdpProcessingJob,
        IdpExtractedHeader,
        IdpExtractedLineItem
    )
    from agentcore.services.idp.extraction import save_extraction_results
    from sqlmodel import select

    agent_id = uuid4()
    doc_id = uuid4()
    job_id = uuid4()

    async with session_scope() as session:
        # Create base Agent
        base_agent = Agent(
            id=agent_id,
            name="Extraction Helper Agent",
        )
        session.add(base_agent)
        await session.flush()

        idp_agent = IdpAgent(
            id=agent_id,
            agent_id=agent_id,
            extraction_mode="dynamic_prompting",
        )
        session.add(idp_agent)
        await session.flush()

        doc = IdpDocument(
            id=doc_id,
            agent_id=agent_id,
            original_filename="test.pdf",
            file_path="/tmp/test.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            source="upload",
            status="queued"
        )
        session.add(doc)
        await session.flush()

        job = IdpProcessingJob(
            id=job_id,
            document_id=doc_id,
            agent_id=agent_id,
            status="queued"
        )
        session.add(job)
        await session.commit()

    # Define mock extracted result
    extraction_result = {
        "headers": {
            "invoice_number": {
                "value": "INV-TEST-100",
                "confidence": 0.95,
                "reasoning": "Top left location"
            },
            "date": {
                "value": "2026-06-08",
                "confidence": 0.85,
                "reasoning": "Top right"
            }
        },
        "line_items": [
            {
                "row_index": 0,
                "columns": [
                    {
                        "column_name": "item",
                        "value": "Widgets",
                        "confidence": 0.9,
                        "reasoning": "First item"
                    }
                ]
            }
        ]
    }

    # Define OCR tokens with matching word to check source location mapping
    ocr_tokens = [
        {
            "text": "INV-TEST-100",
            "bounding_box": [[10, 10], [50, 10], [50, 20], [10, 20]],
            "confidence": 0.99,
            "page_number": 1
        },
        {
            "text": "Widgets",
            "bounding_box": [[100, 100], [200, 100], [200, 120], [100, 120]],
            "confidence": 0.98,
            "page_number": 1
        }
    ]

    async with session_scope() as session:
        overall_conf = await save_extraction_results(
            session=session,
            document_id=doc_id,
            job_id=job_id,
            extraction_result=extraction_result,
            ocr_tokens=ocr_tokens
        )

    # Simple average of 0.95, 0.85, and 0.9 should be 0.9
    assert abs(overall_conf - 0.9) < 1e-4

    async with session_scope() as session:
        # Check persisted headers
        headers = (await session.exec(
            select(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job_id)
        )).all()
        assert len(headers) == 2
        header_map = {h.field_name: h for h in headers}
        assert "invoice_number" in header_map
        assert header_map["invoice_number"].extracted_value == "INV-TEST-100"
        assert float(header_map["invoice_number"].confidence_score) == 0.95
        assert header_map["invoice_number"].reasoning_trace == "Top left location"
        assert header_map["invoice_number"].source_location is not None
        assert header_map["invoice_number"].source_location["page_number"] == 1
        assert header_map["invoice_number"].source_location["bounding_box"] == [[10, 10], [50, 10], [50, 20], [10, 20]]

        assert "date" in header_map
        assert header_map["date"].extracted_value == "2026-06-08"
        # Date wasn't in ocr_tokens, so source_location should be None
        assert header_map["date"].source_location is None

        # Check line items
        line_items = (await session.exec(
            select(IdpExtractedLineItem).where(IdpExtractedLineItem.job_id == job_id)
        )).all()
        assert len(line_items) == 1
        assert line_items[0].column_name == "item"
        assert line_items[0].extracted_value == "Widgets"
        assert line_items[0].row_index == 0
        assert line_items[0].source_location is not None
        assert line_items[0].source_location["bounding_box"] == [[100, 100], [200, 100], [200, 120], [100, 120]]

        # Check updated document confidence
        db_doc = await session.get(IdpDocument, doc_id)
        assert float(db_doc.overall_confidence) == overall_conf

    # 6. Cleanup
    async with session_scope() as session:
        # Delete created entries (cascade will clean extraction results)
        db_job = await session.get(IdpProcessingJob, job_id)
        if db_job:
            await session.delete(db_job)
        db_doc = await session.get(IdpDocument, doc_id)
        if db_doc:
            await session.delete(db_doc)
        db_idp = await session.get(IdpAgent, agent_id)
        if db_idp:
            await session.delete(db_idp)
        db_agent = await session.get(Agent, agent_id)
        if db_agent:
            await session.delete(db_agent)
        await session.commit()
