from __future__ import annotations

import copy
import json
from typing import Any

from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import FloatInput, HandleInput, MessageTextInput, MultiselectInput, Output
from agentcore.schema.message import Message


class IDPDocumentClassifier(Node):
    display_name = "Document Classifier"
    description = (
        "Uses an LLM to detect the document type (invoice, contract, etc.) "
        "from your Field Configurations and tags the document for downstream routing."
    )
    icon = "Tag"
    name = "DocumentClassifier"

    inputs = [
        HandleInput(
            name="document",
            display_name="Document",
            input_types=["Message"],
            required=True,
        ),
        HandleInput(
            name="llm",
            display_name="Language Model",
            input_types=["LanguageModel"],
            required=False,
            info="Connect any model from the Models sidebar. Falls back to model_name if not connected.",
        ),
        MultiselectInput(
            name="document_types",
            display_name="Document Types",
            options=[],
            value=[],
            info="Select the document types to classify against. Populated from your Field Configurations' doc_type values.",
        ),
        FloatInput(
            name="confidence_threshold",
            display_name="Min Confidence Score",
            value=0.75,
            advanced=True,
            info="Predictions below this threshold are marked as 'unknown'.",
        ),
        MessageTextInput(
            name="model_name",
            display_name="LLM Model",
            value="gpt-4o",
            advanced=True,
            info="Model to use when no Language Model node is connected.",
        ),
    ]

    outputs = [
        Output(display_name="Classified Document", name="classified_document", method="classify"),
    ]

    # ── build config ─────────────────────────────────────────────────────

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        if field_name in (None, "document_types"):
            build_config["document_types"]["options"] = self._fetch_doc_types()
        return build_config

    def _fetch_doc_types(self) -> list[str]:
        try:
            from sqlmodel import Session, select
            from agentcore.services.deps import get_service
            from agentcore.services.schema import ServiceType
            from agentcore.services.database.models.idp.config import IdpFieldConfiguration

            db_service = get_service(ServiceType.DATABASE_SERVICE)
            sync_engine = db_service.engine.sync_engine
            with Session(sync_engine) as session:
                rows = session.exec(
                    select(IdpFieldConfiguration.doc_type)
                    .where(
                        IdpFieldConfiguration.deleted_at.is_(None),
                        IdpFieldConfiguration.doc_type.isnot(None),
                    )
                    .distinct()
                    .order_by(IdpFieldConfiguration.doc_type)
                ).all()
                return [r for r in rows if r]
        except Exception as exc:
            logger.warning(f"[DocumentClassifier] Could not fetch doc types: {exc}")
            return []

    # ── classify ─────────────────────────────────────────────────────────

    async def classify(self) -> Message:
        from langchain_core.messages import HumanMessage, SystemMessage
        from agentcore.services.deps import session_scope
        from agentcore.services.database.models.idp.config import IdpFieldConfiguration
        from agentcore.services.idp.classification import ClassificationResult
        from sqlalchemy.sql import func
        from sqlmodel import select

        src = self.document
        text = src.text if isinstance(src, Message) else str(src)
        selected_types: list[str] = list(self.document_types) if self.document_types else []

        if not selected_types:
            logger.warning("[DocumentClassifier] No document types selected — returning 'unknown'.")
            return self._tag_message(src, "unknown", 0.0, "No document types configured.")

        # Build type list with descriptions from field configs
        async with session_scope() as session:
            type_descriptions: dict[str, str] = {}
            for dt in selected_types:
                config = (await session.exec(
                    select(IdpFieldConfiguration)
                    .where(
                        IdpFieldConfiguration.doc_type == dt,
                        IdpFieldConfiguration.deleted_at.is_(None),
                        IdpFieldConfiguration.is_active == True,
                    )
                    .limit(1)
                )).first()
                if config and config.description:
                    type_descriptions[dt] = config.description
                else:
                    type_descriptions[dt] = dt

        types_block = "\n".join(
            f"- {dt}: {desc}" for dt, desc in type_descriptions.items()
        )
        system_prompt = (
            "You are an expert Document Classifier.\n"
            "Classify the document text into exactly one of the following types:\n\n"
            f"{types_block}\n\n"
            "If the document does not match any of these types, return 'unknown'.\n"
            "Respond ONLY with valid JSON matching this schema:\n"
            '{"predicted_type": "<type>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}'
        )
        user_prompt = f"Document Text:\n{text[:4000]}"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        llm_model = self.llm
        if llm_model is None:
            llm_model = self._build_fallback_model()

        result = await self._invoke_llm(llm_model, messages)

        # Defensive: never trust the model call to return a result object (structured output can yield
        # None). getattr-with-default keeps the classifier from crashing the whole run.
        predicted_type = getattr(result, "predicted_type", None) or "unknown"
        confidence = max(0.0, min(1.0, float(getattr(result, "confidence", 0.0) or 0.0)))
        reasoning = getattr(result, "reasoning", None) or ""

        if confidence < float(self.confidence_threshold):
            predicted_type = "unknown"

        # Persist predicted_type to IdpDocument if document carries a document_id
        await self._persist_predicted_type(src, predicted_type)

        self.status = f"Classified as '{predicted_type}' (confidence {confidence:.0%})"
        return self._tag_message(src, predicted_type, confidence, reasoning)

    # ── helpers ───────────────────────────────────────────────────────────

    def _build_fallback_model(self):
        try:
            from agentcore.services.deps import get_settings_service
            from agentcore.services.model_service_client import MicroserviceChatModel
            settings = get_settings_service().settings
            return MicroserviceChatModel(
                service_url=settings.model_service_url,
                service_api_key=settings.model_service_api_key,
                registry_model_id=None,
                provider="openai",
                model=str(self.model_name),
            )
        except Exception as exc:
            logger.warning(f"[DocumentClassifier] Could not build fallback model: {exc}")
            return None

    async def _invoke_llm(self, llm_model, messages) -> "ClassificationResult":
        from agentcore.services.idp.classification import ClassificationResult

        if llm_model is None:
            return ClassificationResult(predicted_type="unknown", confidence=0.0, candidates={})

        if hasattr(llm_model, "with_structured_output"):
            try:
                structured = llm_model.with_structured_output(ClassificationResult)
                structured_result = await structured.ainvoke(messages)
                if structured_result is not None:
                    return structured_result
                # Some models return None from structured output → fall through to plain JSON parsing.
                logger.warning("[DocumentClassifier] Structured output returned None — falling back to plain invoke.")
            except Exception as exc:
                logger.warning(f"[DocumentClassifier] Structured output failed, falling back: {exc}")

        try:
            response = await llm_model.ainvoke(messages)
            content = response.content if hasattr(response, "content") else str(response)
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                lines = lines[1:] if lines[0].strip().startswith("```") else lines
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines).strip()
            parsed = json.loads(content)
            return ClassificationResult(
                predicted_type=parsed.get("predicted_type", "unknown"),
                confidence=float(parsed.get("confidence", 0.0)),
                candidates=parsed.get("candidates", {}),
            )
        except Exception as exc:
            logger.error(f"[DocumentClassifier] LLM invocation failed: {exc}")
            return ClassificationResult(predicted_type="unknown", confidence=0.0, candidates={})

    async def _persist_predicted_type(self, src: Any, predicted_type: str) -> None:
        try:
            from agentcore.services.deps import session_scope
            from agentcore.services.database.models.idp.documents import IdpDocument
            from agentcore.services.idp.graph_native.payload import effective_payload
            from uuid import UUID

            # document_id rides in the IDP payload (+ shared channel) — NOT top-level additional_kwargs,
            # so read it there first (the old top-level read always skipped the DB write in native mode).
            doc_id_raw = effective_payload(self, src).get("document_id") or (
                (src.additional_kwargs or {}).get("document_id") if isinstance(src, Message) else None
            )

            if not doc_id_raw:
                return

            doc_id = UUID(str(doc_id_raw))
            async with session_scope() as session:
                doc = await session.get(IdpDocument, doc_id)
                if doc:
                    doc.predicted_type = predicted_type
                    session.add(doc)
                    await session.commit()
        except Exception as exc:
            logger.debug(f"[DocumentClassifier] Could not persist predicted_type: {exc}")

    def _tag_message(self, src: Any, predicted_type: str, confidence: float, reasoning: str) -> Message:
        from agentcore.services.idp.graph_native.payload import carry

        # Put predicted_type in the IDP payload so routers resolve document_type via the shared channel,
        # and keep the richer classification block on the Message for the extractor's multi-config routing.
        msg = carry(src, predicted_type=predicted_type)
        msg.additional_kwargs["classification"] = {
            "type": predicted_type,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        return msg
