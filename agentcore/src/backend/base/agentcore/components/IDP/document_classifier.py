from __future__ import annotations

import copy
import json
from typing import Any

from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import FloatInput, HandleInput, MultiselectInput, Output
from agentcore.schema.message import Message


class IDPDocumentClassifier(Node):
    display_name = "Document Classifier"
    description = (
        "Detects the document type (invoice, contract, etc.) from your Field Configurations and tags "
        "the document for downstream routing. Uses the connected model when available; without one it "
        "falls back to offline keyword matching."
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
            info="Connect any model from the Models sidebar. Without one, the classifier matches document types by keyword (offline).",
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
        from agentcore.services.deps import session_scope
        from agentcore.services.database.models.idp.config import IdpFieldConfiguration
        from agentcore.services.idp.graph_native.payload import (
            TERMINAL_DECISIONS,
            carry,
            effective_payload,
        )
        from sqlmodel import select

        src = self.document

        # Terminal-decision short-circuit: if an upstream node (e.g. Document Splitter) has
        # already finalised the document (split/skipped/dropped/failed), pass through without
        # any LLM call or DB write.  Preserves the decision in the outgoing payload.
        _decision = str(effective_payload(self, src).get("decision") or "").strip().lower()
        if _decision in TERMINAL_DECISIONS:
            self.status = f"passthrough ({_decision})"
            return carry(src)

        text = src.text if isinstance(src, Message) else str(src)
        selected_types: list[str] = list(self.document_types) if self.document_types else []

        if not selected_types:
            logger.warning("[DocumentClassifier] No document types selected — returning 'unknown'.")
            return self._tag_message(src, "unknown", 0.0, "No document types configured.")

        # Build type -> description map from field configs to enrich the classification prompt
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
                type_descriptions[dt] = config.description if (config and config.description) else dt

        if self.llm is None:
            # No model connected → offline keyword classification (rules fallback).
            from agentcore.services.idp.classification import classify_via_rules
            result = classify_via_rules(selected_types, text, descriptions=type_descriptions)
        else:
            result = await self._invoke_llm(self.llm, doc_types=selected_types, text=text, descriptions=type_descriptions)

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

    async def _invoke_llm(self, llm_model, messages=None, *, doc_types=None, text: str = "", descriptions=None) -> "ClassificationResult":
        from agentcore.services.idp.classification import ClassificationResult, classify_via_llm

        if llm_model is None:
            return ClassificationResult(predicted_type="unknown", confidence=0.0, candidates={})
        return await classify_via_llm(llm_model, doc_types or [], text, descriptions=descriptions)

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
                    # don't clobber a meaningful pre-type (e.g. the Document Splitter's) with 'unknown'
                    if predicted_type != "unknown" or not doc.predicted_type:
                        doc.predicted_type = predicted_type
                        session.add(doc)
                        await session.commit()
        except Exception as exc:
            logger.debug(f"[DocumentClassifier] Could not persist predicted_type: {exc}")

    def _tag_message(self, src: Any, predicted_type: str, confidence: float, reasoning: str) -> Message:
        from agentcore.services.idp.graph_native.payload import carry

        # Put BOTH predicted_type and the full classification block into the IDP working-set payload so
        # the native engine preserves + shares them to the extractor (top-level additional_kwargs is
        # stripped between nodes).
        return carry(
            src,
            predicted_type=predicted_type,
            classification={"type": predicted_type, "confidence": confidence, "reasoning": reasoning},
        )
