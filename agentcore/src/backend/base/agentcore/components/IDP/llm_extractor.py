from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import DataInput, DropdownInput, HandleInput, MessageTextInput, MultiselectInput, Output
from agentcore.schema.data import Data
from agentcore.schema.message import Message
from agentcore.services.idp.graph_native.payload import carry


class IDPLLMExtractor(Node):
    display_name = "AI Field Extractor"
    description = (
        "Extracts structured fields from a document using an LLM. "
        "Choose between writing a custom prompt or selecting a saved Field Configuration."
    )
    icon = "BrainCircuit"
    name = "LLMExtractor"

    inputs = [
        HandleInput(
            name="document",
            display_name="OCR Text / Document",
            input_types=["Message"],
            required=True,
        ),
        HandleInput(
            name="llm",
            display_name="Language Model",
            input_types=["LanguageModel"],
            required=False,
            info="Connect any model from the Models sidebar.",
        ),
        DropdownInput(
            name="extraction_mode",
            display_name="Extraction Mode",
            # Config-driven only: pick a saved Field Configuration. field_configuration honors Input
            # Mode (text layer for digital docs, page images for image/scanned docs); multimodal_config
            # always reads the page images with a vision model. Author fields on the Field
            # Configurations page.
            options=["field_configuration", "multimodal_config"],
            value="field_configuration",
            info="field_configuration: extract using a saved schema (text, or vision when there's no text layer). multimodal_config: always read the page images with a vision model.",
        ),
        MessageTextInput(
            name="config_name",
            display_name="Field Configuration Name",
            value="",
            info="Name of the saved Field Configuration from the Configuration page. Used when Extraction Mode is 'field_configuration' or 'multimodal_config'.",
        ),
        MultiselectInput(
            name="config_names",
            display_name="Field Configurations (Multi-Type)",
            options=[],
            value=[],
            info=(
                "Select multiple Field Configurations for multi-type routing. "
                "When a Document Classifier is upstream, the config whose doc_type matches the "
                "classified document type is used automatically. Documents with no matching config "
                "are marked 'skipped'. Leave empty to use the single 'Field Configuration Name' above."
            ),
        ),
        DropdownInput(
            name="unmatched_action",
            display_name="On unmatched type",
            options=["skip", "extract_anyway", "drop"],
            value="skip",
            advanced=True,
            info=(
                "What to do when a document's classified type matches NONE of the selected Field "
                "Configurations (e.g. a medical report in an invoice-only agent). 'skip' (default): mark it "
                "Skipped for review. 'extract_anyway': force it through the first selected config. 'drop': "
                "discard it (soft-deleted, hidden from Processed Docs). Requires a Document Classifier upstream."
            ),
        ),
    ]

    outputs = [
        Output(display_name="Extracted Data", name="extracted_data", method="extract"),
    ]

    # ── build config (field visibility + dropdown population) ─────────────────

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None) -> dict:
        # Both modes (field_configuration, multimodal_config) are config-driven — keep the config
        # pickers' options fresh.
        if field_name in ("extraction_mode", "config_name", "config_names"):
            names = self._fetch_config_names()
            if "config_name" in build_config:
                build_config["config_name"]["options"] = names
            if "config_names" in build_config:
                build_config["config_names"]["options"] = names
        return build_config

    def _fetch_config_names(self) -> list[str]:
        try:
            from sqlmodel import Session, select

            from agentcore.services.deps import get_service
            from agentcore.services.schema import ServiceType
            from agentcore.services.database.models.idp.config import IdpFieldConfiguration

            db_service = get_service(ServiceType.DATABASE_SERVICE)
            sync_engine = db_service.engine.sync_engine
            with Session(sync_engine) as session:
                names = session.exec(
                    select(IdpFieldConfiguration.name)
                    .where(IdpFieldConfiguration.deleted_at.is_(None))
                    .order_by(IdpFieldConfiguration.name)
                ).all()
                return [n for n in names if n]
        except Exception as exc:
            logger.warning(f"[AIFieldExtractor] Could not fetch field configs: {exc}")
            return []


    async def _resolve_config_name_from_classification(self, src: Any = None) -> str | None:
        """When config_names multi-select is used and classification metadata is present,
        find which selected config's doc_type matches the classified type.
        Returns the config name to use, or None if no match (→ skip)."""
        # The classifier gate applies to a SINGLE selected config too (not just the multi list): fall
        # back to the single Field Configuration when no multi-select list is set, so a classifier that
        # says "this isn't your type" routes to skip even with one config.
        config_names: list[str] = list(self.config_names) if self.config_names else []
        if not config_names:
            single = str(getattr(self, "config_name", "") or "").strip()
            config_names = [single] if single else []
        if not config_names:
            return None

        from agentcore.services.idp.graph_native.payload import effective_payload

        src = src if src is not None else self.document
        # Read the classification shared-channel-aware so it survives intermediate nodes (chunking,
        # etc.); fall back to top-level additional_kwargs for compatibility.
        payload = effective_payload(self, src)
        classification = payload.get("classification") or (
            (src.additional_kwargs or {}).get("classification", {}) if isinstance(src, Message) else {}
        )
        # A truthy 'type' means a classifier ran (it emits "unknown", never "", for a non-match). But an
        # `error` classification (model failure/rate-limit) is NOT a real result — treat it as "no
        # classifier" so an errored doc falls through to extraction instead of being skipped + lost.
        classifier_ran = bool(classification.get("type")) and not classification.get("error")
        classified_type = (classification.get("type") or "").strip().lower()
        if not classified_type or classified_type == "unknown":
            # A Document Classifier RAN and could not identify the type -> treat as no-match so the
            # extractor's 'On unmatched type' action decides (skip/extract_anyway/drop), even for a single
            # selected config. NO classifier upstream -> nothing to route on: a single config is used
            # directly; several can't be disambiguated -> skip.
            if classifier_ran:
                return None
            return config_names[0] if len(config_names) == 1 else None

        # A config's `doc_type` is optional — an auto-cloned config never had one copied over, and a
        # hand-made config often leaves it blank. Fall back to the config NAME, which is what the fixed
        # pipeline already routes on (`_build_config_name_map` keys the map by name). Matching on
        # doc_type ALONE meant a doc_type-less config could never be selected, and the extractor bailed
        # out with `extracted={}` — a silent zero-field run.
        def _routes_to(doc_type: str | None, name: str) -> bool:
            return ((doc_type or name) or "").strip().lower() == classified_type

        try:
            from agentcore.services.idp.field_defs import frozen_doc_type, has_frozen_defs

            # A published run routes on the doc_types frozen at publish, so editing a config's doc_type
            # (or deleting the config) cannot silently re-route an already-published agent.
            if has_frozen_defs():
                for name in config_names:
                    if _routes_to(frozen_doc_type(name), name):
                        return name
                return None

            from agentcore.services.deps import session_scope
            from agentcore.services.database.models.idp.config import IdpFieldConfiguration
            from sqlmodel import select

            async with session_scope() as session:
                for name in config_names:
                    if not name:
                        continue
                    config = (await session.exec(
                        select(IdpFieldConfiguration)
                        .where(
                            IdpFieldConfiguration.name == name,
                            IdpFieldConfiguration.deleted_at.is_(None),
                        )
                        .limit(1)
                    )).first()
                    if config and _routes_to(config.doc_type, config.name):
                        return config.name
        except Exception as exc:
            logger.warning(f"[AIFieldExtractor] multi-config lookup failed: {exc}")

        return None  # no match

    def _decide_route(self, text: str) -> str:
        """Text vs vision, honoring the node's Input Mode (auto/text/vision/text_vision).

        - ``text``: OCR/native text only (even if empty — the user's explicit choice).
        - ``vision`` / ``text_vision``: read the page images with a vision model.
        - ``auto`` (default): use the text layer when it exists (digital doc, or an upstream OCR node
          produced text); otherwise there's nothing to read as text (an image / scanned doc with no
          OCR node), so read the page images — exactly what the Input Mode tooltip promises.
        """
        mode = str(getattr(self, "input_mode", "") or "auto").strip().lower()
        has_text = bool(text and text.strip())
        if mode == "text":
            return "text"
        if mode in ("vision", "text_vision"):
            return "vision"
        return "text" if has_text else "vision"

    async def _ensure_vision_capable(self) -> None:
        """Guard the vision path: if we can determine the connected model is NOT vision-capable, fail with
        a clear, actionable error instead of sending page images to a text-only model (which returns
        garbage or a provider error). Unknown capability → proceed (never false-block a valid model).
        (Codex #3)"""
        llm = self.llm
        rmid = getattr(llm, "registry_model_id", None)
        if not rmid:
            return
        try:
            from uuid import UUID

            from agentcore.services.database.models.model_registry.model import ModelRegistry
            from agentcore.services.deps import session_scope
            from agentcore.services.idp.pipeline import _supports_vision

            async with session_scope() as session:
                reg = await session.get(ModelRegistry, UUID(str(rmid)))
            if reg is not None and not _supports_vision(reg):
                name = getattr(reg, "name", None) or getattr(llm, "model", None) or "the connected model"
                raise ValueError(
                    f"This document has no text layer, so it must be read as page images, but '{name}' is "
                    f"not vision-capable. Connect a model marked 'Supports vision', or add a PaddleOCR node "
                    f"so the text is extracted first."
                )
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — a resolution failure must not block a valid model
            logger.debug(f"[AIFieldExtractor] vision-capability check skipped: {exc}")

    async def _extract_via_vision(self, src: Any, prompt_or_config_id, session=None) -> dict:
        """Extract straight from the page images (no OCR text) — for image / scanned documents wired
        without an OCR node. Loads the document bytes from storage, renders them, and runs the vision
        extractor with either a field-configuration id or a free-text prompt."""
        import os
        import tempfile

        from agentcore.services.idp.extraction import extract_multimodal
        from agentcore.services.idp.graph_native.payload import effective_payload, load_bytes

        await self._ensure_vision_capable()
        payload = effective_payload(self, src)
        file_bytes = await load_bytes(payload)
        if not file_bytes:
            raise ValueError("No document bytes available for vision extraction.")
        suffix = "." + str(payload.get("file_type") or "pdf").lstrip(".")
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(file_bytes)
            self.status = "extracting via vision (no OCR text)"
            return await extract_multimodal(
                file_path=tmp,
                prompt_or_config_id=prompt_or_config_id,
                llm_model=self.llm,
                session=session,
            )
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass

    async def _mark_skipped(self, classified_type: str, reason: str | None = None) -> Data:
        """Persist 'skipped' status on the document and return empty Data. The human-readable REASON
        is surfaced via the node status + flow log (IdpDocument has no error_message column — that
        field is on IdpProcessingJob)."""
        src = self.document
        # Explain WHY, distinguishing the two skip causes so the user can fix the flow.
        config_names = list(self.config_names) if self.config_names else []
        if reason is None:
            if (not classified_type or classified_type.strip().lower() in ("", "unknown")) and len(config_names) > 1:
                reason = (
                    "Multiple field configurations are selected but there is no Document Classifier node to "
                    "route between them. Add a Document Classifier, or select a single field configuration."
                )
            else:
                reason = f"No selected field configuration matches the document type '{classified_type}'."

        try:
            from agentcore.services.deps import session_scope
            from agentcore.services.database.models.idp.documents import IdpDocument
            from agentcore.services.idp.graph_native.payload import effective_payload
            from uuid import UUID

            # document_id rides in the IDP payload (additional_kwargs["idp"]) + the shared channel.
            doc_id_raw = effective_payload(self, src).get("document_id") or (
                (src.additional_kwargs or {}).get("document_id") if isinstance(src, Message) else None
            )

            if doc_id_raw:
                doc_id = UUID(str(doc_id_raw))
                async with session_scope() as session:
                    doc = await session.get(IdpDocument, doc_id)
                    if doc:
                        # IdpDocument has no error_message column (it's on IdpProcessingJob); the
                        # reason is surfaced via node status + flow log, not persisted on the doc.
                        doc.status = "skipped"
                        session.add(doc)
                        await session.commit()
        except Exception as exc:
            logger.debug(f"[AIFieldExtractor] Could not mark document as skipped: {exc}")

        logger.warning(f"[AIFieldExtractor] document skipped: {reason}")
        self.status = f"Skipped — {reason}"
        return carry(src, text="", decision="skipped", skip_reason=reason)

    async def _handle_unmatched(self, classified_type: str, config_names: list, classifier_ran: bool) -> "Data | None":
        """Apply the 'On unmatched type' action when the classified type matches no selected config.
        extract_anyway/drop apply ONLY when a Document Classifier actually RAN (the control requires one);
        with no classifier there is nothing to route on, so always skip with a helpful reason. Returns
        terminal Data for skip/drop; None for extract_anyway (caller proceeds with _override_config_name set)."""
        if not classifier_ran:
            return await self._mark_skipped(classified_type)   # no classifier -> skip with the 'add a Classifier' reason
        action = str(getattr(self, "unmatched_action", "skip") or "skip").strip()
        if action == "extract_anyway":
            self._override_config_name = config_names[0] if config_names else None
            self.status = f"Unmatched type '{classified_type}' — extracting anyway with '{self._override_config_name}'."
            return None
        if action == "drop":
            return await self._drop_document(classified_type)
        return await self._mark_skipped(
            classified_type,
            reason=f"The classified type '{classified_type}' matches none of the selected field configurations.",
        )

    async def _drop_document(self, classified_type: str) -> Data:
        """unmatched_action='drop': soft-delete the document (hidden from Processed Docs) + return empty Data."""
        from datetime import datetime, timezone
        src = self.document
        try:
            from agentcore.services.deps import session_scope
            from agentcore.services.database.models.idp.documents import IdpDocument
            from agentcore.services.idp.graph_native.payload import effective_payload
            from uuid import UUID
            doc_id_raw = effective_payload(self, src).get("document_id") or (
                (src.additional_kwargs or {}).get("document_id") if isinstance(src, Message) else None
            )
            if doc_id_raw:
                doc_id = UUID(str(doc_id_raw))
                async with session_scope() as session:
                    doc = await session.get(IdpDocument, doc_id)
                    if doc:
                        doc.deleted_at = datetime.now(timezone.utc)
                        doc.status = "skipped"   # valid CheckConstraint status; deleted_at hides it from review
                        # (IdpDocument has no error_message column — reason is in the flow log.)
                        session.add(doc)
                        await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[AIFieldExtractor] Could not drop document: {exc}")
        self.status = f"Dropped — unmatched type '{classified_type}'."
        return carry(src, text="", decision="dropped")

    async def extract(self) -> Data:
        src = self.document

        # Terminal-decision short-circuit: if an upstream node has already finalised the document
        # (split / skipped / dropped / failed), pass through without any model call or DB write.
        from agentcore.services.idp.graph_native.payload import (
            TERMINAL_DECISIONS,
            effective_payload,
        )
        _first = src[0] if isinstance(src, (list, tuple)) and src else src
        _decision = str(effective_payload(self, _first).get("decision") or "").strip().lower()
        if _decision in TERMINAL_DECISIONS:
            self.status = f"passthrough ({_decision})"
            return carry(_first)

        # Long-document support: the Chunking Strategy node feeds a LIST of chunk Messages. Extract each
        # chunk with the resolved Field Configuration and merge the per-chunk JSONs into one result — so
        # the user tunes chunk size on the Chunking node and the extractor handles any document length.
        if isinstance(src, (list, tuple)):
            return await self._extract_chunks(list(src))

        text = src.text if isinstance(src, Message) else str(src)

        # A Field Configuration is mandatory (enforced at publish time on the canvas).
        config_names: list[str] = list(self.config_names) if self.config_names else []
        single_config = str(getattr(self, "config_name", "") or "").strip()
        logger.info(
            f"[AIFieldExtractor] mode={self.extraction_mode!r} config_name={single_config!r} "
            f"config_names={config_names!r}"
        )
        # Neither the single field nor the multi field is set → clear, actionable error (not a cryptic
        # "config 'None' not found"). The single 'Field Configuration' is enough; the multi field is
        # only needed for multi-type routing.
        if not single_config and not config_names:
            self.status = "No Field Configuration selected."
            return carry(
                src,
                text="",
                extracted={"error": "No Field Configuration selected — pick one in the AI Field Extractor."},
            )

        # Classifier gate: whenever a Document Classifier RAN — for a SINGLE config or a multi list —
        # route to the matching config, or skip/drop per 'On Unmatched Type'. Without a classifier, the
        # single config is used directly (unchanged). This makes a single "Invoice" config skip a
        # medical/aadhaar doc instead of force-extracting it.
        selected_configs = config_names or ([single_config] if single_config else [])
        if selected_configs and self.extraction_mode == "field_configuration":
            from agentcore.services.idp.graph_native.payload import effective_payload

            classification = effective_payload(self, src).get("classification") or (
                (getattr(src, "additional_kwargs", None) or {}).get("classification", {}) if isinstance(src, Message) else {}
            )
            classifier_ran = bool(classification.get("type")) and not classification.get("error")  # errored classifier (rate-limit/timeout) -> fall through, don't skip
            classified_type = (classification.get("type") or "unknown").strip()

            if classifier_ran:
                matched_config = await self._resolve_config_name_from_classification()
                if matched_config is None:
                    unmatched = await self._handle_unmatched(classified_type, selected_configs, True)
                    if unmatched is not None:
                        return unmatched        # skip / drop -> terminal
                    # extract_anyway -> _handle_unmatched set self._override_config_name; fall through
                else:
                    self._override_config_name = matched_config
            else:
                self._override_config_name = None
        else:
            self._override_config_name = None

        # The extractor is config-driven (Field Configurations). Both modes build their prompt/schema
        # inside extract_named_config / extract_multimodal from the config — no pre-build needed here.
        try:
            if self.extraction_mode == "field_configuration":
                effective_config_name = (
                    self._override_config_name or single_config or (config_names[0] if config_names else None)
                )
                if not self.llm:
                    raw = f"[No model connected — config would be: {effective_config_name}]"
                    extracted = {"error": "No model connected"}
                else:
                    from agentcore.services.deps import session_scope
                    from agentcore.services.idp.extraction import extract_named_config
                    from agentcore.services.idp.field_defs import resolve_config_id

                    async with session_scope() as session:
                        # Prefers the id this run's published snapshot froze for the name; a draft run
                        # resolves it live, as before.
                        config_id = await resolve_config_id(session, effective_config_name)

                        # Honor Input Mode: text path when there's a text layer (or explicit text mode),
                        # else read the page images with the vision model (image/scanned docs) — otherwise
                        # all fields come back null.
                        if self._decide_route(text) == "text":
                            extracted = await extract_named_config(
                                session=session,
                                ocr_text=text,
                                field_config_id=config_id,
                                llm_model=self.llm,
                            )
                        else:
                            extracted = await self._extract_via_vision(src, config_id, session)
                        raw = json.dumps(extracted, indent=2)

            elif self.extraction_mode == "multimodal_config":
                # Always vision: read the page images with a saved field configuration.
                effective_config_name = (
                    self._override_config_name or single_config or (config_names[0] if config_names else None)
                )
                if not self.llm:
                    raw = f"[No model connected — vision config would be: {effective_config_name}]"
                    extracted = {"error": "No model connected"}
                else:
                    from agentcore.services.deps import session_scope
                    from agentcore.services.idp.field_defs import resolve_config_id

                    async with session_scope() as session:
                        config_id = await resolve_config_id(session, effective_config_name)
                        # Storage-based vision (native docs live in storage, not on a local path).
                        extracted = await self._extract_via_vision(src, config_id, session)
                        raw = json.dumps(extracted, indent=2)

            headers_count = len(extracted.get("headers", {})) if isinstance(extracted, dict) else 0
            line_items_count = len(extracted.get("line_items", [])) if isinstance(extracted, dict) else 0
            logger.info(f"[AIFieldExtractor] raw extraction JSON: {json.dumps(extracted, default=str)[:200]}")
            self.status = f"Extracted {headers_count} header(s) and {line_items_count} line item(s)"
            # Carry the IDP working-set forward (document_id, job_id, extracted, …) in the payload so
            # the terminal sink can persist. Read it with get_payload(...)["extracted"] — NOT .data
            # (the engine strips additional_kwargs once .data is set, which would lose document_id).
            return carry(src, text=raw, extracted=extracted)
        except Exception as exc:
            self.status = f"Error: {exc}"
            logger.error(f"[AIFieldExtractor] Extraction failed: {exc}")
            return carry(src, text="", extracted={"error": str(exc)})

    async def _extract_chunks(self, chunks: list) -> Data:
        """Long-document path: extract each chunk (from the Chunking Strategy) with the resolved Field
        Configuration, then merge the per-chunk JSONs into one result via long_doc.merge_chunk_extractions
        (keep-highest-confidence). The Chunk Aggregator node is NOT needed — the merge happens here."""
        from agentcore.services.idp.graph_native.payload import carry

        chunks = [c for c in chunks if c is not None]
        if not chunks:
            return carry(self.document if not isinstance(self.document, (list, tuple)) else None,
                         text="", extracted={"error": "no chunks to extract"})
        rep = chunks[0]  # representative chunk — carries the shared payload + classification block

        # Terminal-decision short-circuit: if the representative chunk already carries a terminal
        # decision, pass through without any model call or DB write.
        from agentcore.services.idp.graph_native.payload import (
            TERMINAL_DECISIONS,
            effective_payload,
        )
        _decision = str(effective_payload(self, rep).get("decision") or "").strip().lower()
        if _decision in TERMINAL_DECISIONS:
            self.status = f"passthrough ({_decision})"
            return carry(rep)

        # Resolve the Field Configuration ONCE (single or classifier-routed), same rules as a single doc.
        config_names: list[str] = list(self.config_names) if self.config_names else []
        single_config = str(getattr(self, "config_name", "") or "").strip()
        if not single_config and not config_names:
            self.status = "No Field Configuration selected."
            return carry(rep, text="", extracted={"error": "No Field Configuration selected."})

        self._override_config_name = None
        selected_configs = config_names or ([single_config] if single_config else [])
        if selected_configs and self.extraction_mode == "field_configuration":
            from agentcore.services.idp.graph_native.payload import effective_payload
            from agentcore.schema.message import Message as _Msg

            classification = effective_payload(self, rep).get("classification") or (
                (getattr(rep, "additional_kwargs", None) or {}).get("classification", {}) if isinstance(rep, _Msg) else {}
            )
            classifier_ran = bool(classification.get("type")) and not classification.get("error")  # errored classifier (rate-limit/timeout) -> fall through, don't skip
            classified_type = (classification.get("type") or "unknown").strip()
            if classifier_ran:
                matched = await self._resolve_config_name_from_classification(rep)
                if matched is None:
                    unmatched = await self._handle_unmatched(classified_type, selected_configs, True)
                    if unmatched is not None:
                        return unmatched
                    # extract_anyway -> _override_config_name set; fall through to per-chunk extraction
                else:
                    self._override_config_name = matched
        effective = self._override_config_name or single_config or (config_names[0] if config_names else None)

        if not self.llm:
            self.status = "No model connected."
            return carry(rep, text="", extracted={"error": "No model connected"})

        from agentcore.services.deps import session_scope
        from agentcore.services.idp import long_doc
        from agentcore.services.idp.extraction import extract_named_config
        from agentcore.services.idp.field_defs import resolve_config_id

        results: list[dict] = []
        via_vision = False
        try:
            async with session_scope() as session:
                config_id = await resolve_config_id(session, effective)

                # If NO chunk carries text (a scanned / image document with no OCR text — no OCR node, or
                # PaddleOCR unavailable), read the page IMAGES with the vision model instead of extracting
                # from empty text. Mirrors the single-doc auto path so the same flow works for scanned docs.
                via_vision = not any(((ch.text if isinstance(ch, Message) else str(ch)) or "").strip() for ch in chunks)
                if via_vision:
                    self.status = "No text in chunks — extracting via vision"
                    ex = await self._extract_via_vision(rep, config_id, session)
                    logger.info(f"[AIFieldExtractor] vision raw extraction: {json.dumps(ex, default=str)[:200]}")
                    if isinstance(ex, dict) and (ex.get("headers") or ex.get("line_items")):
                        results.append(ex)
                else:
                    for ch in chunks:
                        ctext = ch.text if isinstance(ch, Message) else str(ch)
                        if not (ctext and ctext.strip()):
                            continue
                        try:
                            ex = await extract_named_config(
                                session=session, ocr_text=ctext, field_config_id=config_id, llm_model=self.llm)
                            logger.info(f"[AIFieldExtractor] chunk raw extraction: {json.dumps(ex, default=str)[:200]}")
                            if isinstance(ex, dict) and (ex.get("headers") or ex.get("line_items")):
                                results.append(ex)
                        except Exception as exc:  # noqa: BLE001 — one bad chunk shouldn't fail the whole doc
                            logger.warning(f"[AIFieldExtractor] chunk extraction failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.status = f"Error: {exc}"
            logger.error(f"[AIFieldExtractor] long-doc extraction failed: {exc}")
            return carry(rep, text="", extracted={"error": str(exc)})

        merged = (long_doc.merge_chunk_extractions(results, strategy="keep_highest_confidence")
                  if results else {"headers": {}, "line_items": []})
        logger.info(f"[AIFieldExtractor] merged extraction JSON: {json.dumps(merged, default=str)[:200]}")
        n_head = len(merged.get("headers", {}))
        n_li = len(merged.get("line_items", []))
        source = "vision (no OCR text)" if via_vision else f"{len(chunks)} chunk(s)"
        self.status = f"Extracted via {source} → {n_head} field(s), {n_li} line item(s)"
        logger.info(f"[AIFieldExtractor] extracted via {source} → {n_head} field(s), {n_li} line item(s)")
        return carry(rep, text=json.dumps(merged, indent=2), extracted=merged)


    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = raw.strip()
        # strip markdown code blocks if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw_extraction": raw}
