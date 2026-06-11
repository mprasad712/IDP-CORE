from __future__ import annotations

import json
from uuid import UUID, uuid4
from loguru import logger
from pydantic import BaseModel, Field
from typing import Dict, Optional
from sqlmodel import select

from agentcore.services.database.models.idp.documents import IdpDocument, IdpDocumentClassification
from agentcore.services.database.models.idp.config import (
    IdpAgent,
    IdpFieldConfiguration,
    IdpFieldConfigHeader,
    IdpFieldConfigLineItem,
)
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.idp.agent_config import resolve_pipeline_config
from agentcore.services.idp.catalogue import DEFAULT_TEMPLATES


class ClassificationResult(BaseModel):
    predicted_type: str = Field(description="The matching document type from the provided list, or 'unknown'.")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0.")
    candidates: Dict[str, float] = Field(description="Dictionary of top candidate types and their confidence scores.")


async def classify_and_persist(
    session,
    document_id: UUID,
    merged_text: str,
    org_id: Optional[UUID],
    *,
    auto_select: bool,
    threshold: float,
) -> dict:
    """Classify the document type from layout text, persisting results and auto-configuring templates.

    Args:
        session: Active DB session.
        document_id: The document UUID.
        merged_text: Layout-reconstructed citation-friendly text.
        org_id: The organization UUID scoping this action.
        auto_select: If True, resolves or clones the Named Config when confidence is high.
        threshold: The confidence limit above which auto-selection takes effect.

    Returns:
        A dict matching {"predicted_type", "confidence", "candidates", "selected_config_id"}.
    """
    # 1. Resolve Document and Agent Config
    doc = await session.get(IdpDocument, document_id)
    if not doc:
        raise ValueError(f"Document {document_id} not found")

    idp_agent = await session.get(IdpAgent, doc.agent_id)
    if not idp_agent:
        raise ValueError(f"IDP agent {doc.agent_id} not found")

    base_agent = await session.get(Agent, idp_agent.agent_id)
    if not base_agent:
        raise ValueError(f"Base agent {idp_agent.agent_id} not found")

    cfg = await resolve_pipeline_config(session, idp_agent, base_agent)
    model_id = cfg.model_id
    if not model_id:
        raise ValueError("No model configured in agent graph for document classification")

    # 2. Build LLM
    from agentcore.services.database.models.model_registry.model import ModelRegistry
    from agentcore.services.model_service_client import MicroserviceChatModel
    from agentcore.services.deps import get_settings_service

    reg = await session.get(ModelRegistry, UUID(str(model_id)))
    if reg is None:
        raise ValueError(f"Model '{model_id}' not found in the model registry")

    settings = get_settings_service().settings
    llm_model = MicroserviceChatModel(
        service_url=settings.model_service_url,
        service_api_key=settings.model_service_api_key,
        registry_model_id=str(model_id),
        provider="openai",
        model=f"idp-classify-{str(model_id)[:8]}",
    )

    # 3. Build Prompt
    types_list = [t["name"] for t in DEFAULT_TEMPLATES]
    types_desc = ", ".join(types_list)

    system_prompt = (
        "You are an expert Document Classifier.\n"
        "Your task is to analyze the document text and classify it into one of the following standard types:\n\n"
        f"{types_desc}\n\n"
        "If the document text does not match any of these types, return 'unknown' as predicted_type.\n"
        "Provide a confidence score (0.0 to 1.0) and include other top candidate types in the candidates dictionary."
    )
    user_prompt = f"Document Text:\n{merged_text[:4000]}"

    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt)
    ]

    # 4. Invoke LLM and Parse
    result = None
    if hasattr(llm_model, "with_structured_output"):
        try:
            structured_model = llm_model.with_structured_output(ClassificationResult)
            result = await structured_model.ainvoke(messages)
        except Exception as e:
            logger.warning(f"[Classification] structured output failed: {e}. Falling back to standard JSON parsing.")

    if result is None:
        try:
            response = await llm_model.ainvoke(messages)
            content = response.content if hasattr(response, "content") else str(response)
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[0].strip().startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines).strip()
            parsed = json.loads(content)
            result = ClassificationResult(
                predicted_type=parsed.get("predicted_type", "unknown"),
                confidence=float(parsed.get("confidence", 0.0)),
                candidates=parsed.get("candidates", {})
            )
        except Exception as e:
            logger.error(f"[Classification] prediction parsing failed: {e}")
            result = ClassificationResult(
                predicted_type="unknown",
                confidence=0.0,
                candidates={}
            )

    # Normalize prediction to match catalog template casing
    predicted_type = result.predicted_type
    matched_template = None
    for t in DEFAULT_TEMPLATES:
        if t["name"].strip().lower() == predicted_type.strip().lower():
            matched_template = t
            predicted_type = t["name"]
            break

    selected_config_id = None

    # 5. Resolve config or Clone global template if auto_select is enabled
    if auto_select and result.confidence >= threshold and matched_template:
        # Check for existing config for this org
        stmt = select(IdpFieldConfiguration).where(
            IdpFieldConfiguration.name == predicted_type,
            IdpFieldConfiguration.org_id == org_id,
            IdpFieldConfiguration.is_template == False,
            IdpFieldConfiguration.deleted_at.is_(None)
        )
        config = (await session.exec(stmt)).first()
        if config:
            selected_config_id = config.id
        else:
            # Look up the global template config
            stmt_template = select(IdpFieldConfiguration).where(
                IdpFieldConfiguration.name == predicted_type,
                IdpFieldConfiguration.org_id.is_(None),
                IdpFieldConfiguration.is_template == True,
                IdpFieldConfiguration.deleted_at.is_(None)
            )
            global_template = (await session.exec(stmt_template)).first()
            if global_template:
                # Clone the global template config for this org_id
                config_id = uuid4()
                new_config = IdpFieldConfiguration(
                    id=config_id,
                    name=global_template.name,
                    description=global_template.description,
                    org_id=org_id,
                    is_template=False,
                    is_active=True,
                    extra=global_template.extra,
                    created_by=doc.uploaded_by,
                    updated_by=doc.uploaded_by,
                )
                # Query and copy headers
                headers = (await session.exec(
                    select(IdpFieldConfigHeader).where(IdpFieldConfigHeader.config_id == global_template.id)
                )).all()
                new_config.headers = [
                    IdpFieldConfigHeader(
                        id=uuid4(),
                        field_name=h.field_name,
                        field_type=h.field_type,
                        is_required=h.is_required,
                        display_order=h.display_order,
                        description=h.description,
                    )
                    for h in headers
                ]
                # Query and copy line items
                line_items = (await session.exec(
                    select(IdpFieldConfigLineItem).where(IdpFieldConfigLineItem.config_id == global_template.id)
                )).all()
                new_config.line_items = [
                    IdpFieldConfigLineItem(
                        id=uuid4(),
                        column_name=li.column_name,
                        column_type=li.column_type,
                        is_required=li.is_required,
                        display_order=li.display_order,
                    )
                    for li in line_items
                ]
                session.add(new_config)
                await session.flush()
                selected_config_id = config_id

    # 6. Save Classification Result
    classification = IdpDocumentClassification(
        id=uuid4(),
        document_id=document_id,
        predicted_type=predicted_type,
        confidence=result.confidence,
        candidates=result.candidates,
        selected_config_id=selected_config_id,
        is_selected=bool(selected_config_id)
    )
    session.add(classification)

    doc.predicted_type = predicted_type
    session.add(doc)
    await session.commit()

    return {
        "predicted_type": predicted_type,
        "confidence": result.confidence,
        "candidates": result.candidates,
        "selected_config_id": selected_config_id
    }
