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
            info="Connect any model from the Models sidebar. Falls back to model_name if not connected.",
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
        MessageTextInput(
            name="model_name",
            display_name="LLM Model",
            value="gpt-4o",
            advanced=True,
            info="Model to use when no model node is connected.",
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
        config_names: list[str] = list(self.config_names) if self.config_names else []
        if not config_names:
            return None

        from agentcore.services.idp.graph_native.payload import get_payload

        src = src if src is not None else self.document
        # A Document Classifier node carries the classification in the IDP payload; fall back to a
        # top-level additional_kwargs for compatibility.
        payload = get_payload(src)
        classification = payload.get("classification") or (
            (src.additional_kwargs or {}).get("classification", {}) if isinstance(src, Message) else {}
        )
        classified_type = (classification.get("type") or "").strip().lower()
        if not classified_type or classified_type == "unknown":
            # No classifier upstream. With ONE selected config there's nothing to route — just use it.
            # With several, we can't disambiguate without a classification, so skip.
            return config_names[0] if len(config_names) == 1 else None

        try:
            from agentcore.services.deps import session_scope
            from agentcore.services.database.models.idp.config import IdpFieldConfiguration
            from sqlmodel import select

            async with session_scope() as session:
                for name in config_names:
                    config = (await session.exec(
                        select(IdpFieldConfiguration)
                        .where(
                            IdpFieldConfiguration.name == name,
                            IdpFieldConfiguration.deleted_at.is_(None),
                        )
                        .limit(1)
                    )).first()
                    if config and config.doc_type and config.doc_type.strip().lower() == classified_type:
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

    async def _mark_skipped(self, classified_type: str) -> Data:
        """Persist 'skipped' status + a human-readable REASON on the document, and return empty Data.

        The reason is stored on ``doc.error_message`` so the Playground can show WHY it was skipped."""
        src = self.document
        # Explain WHY, distinguishing the two skip causes so the user can fix the flow.
        config_names = list(self.config_names) if self.config_names else []
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
                        doc.status = "skipped"
                        doc.error_message = reason
                        session.add(doc)
                        await session.commit()
        except Exception as exc:
            logger.debug(f"[AIFieldExtractor] Could not mark document as skipped: {exc}")

        logger.warning(f"[AIFieldExtractor] document skipped: {reason}")
        self.status = f"Skipped — {reason}"
        return Data(data={})

    async def extract(self) -> Data:
        src = self.document
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

        # Multi-config routing: if config_names is populated, resolve the right config
        # based on the upstream classification or skip if no match.
        if config_names and self.extraction_mode == "field_configuration":
            from agentcore.services.idp.graph_native.payload import get_payload

            classification = get_payload(src).get("classification") or (
                (src.additional_kwargs or {}).get("classification", {}) if isinstance(src, Message) else {}
            )
            classified_type = (classification.get("type") or "unknown").strip()

            # Optional Document Classifier: when ABSENT, a single selected config is used directly;
            # when PRESENT, its type routes to the matching config. Only multiple configs with NO
            # classifier can't be disambiguated → skip.
            matched_config = await self._resolve_config_name_from_classification()
            if matched_config is None:
                return await self._mark_skipped(classified_type)
            # Override single config_name with the matched one
            self._override_config_name = matched_config
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
                    from agentcore.services.database.models.idp.config import IdpFieldConfiguration
                    from sqlmodel import select

                    async with session_scope() as session:
                        config = (await session.exec(
                            select(IdpFieldConfiguration)
                            .where(
                                IdpFieldConfiguration.name == effective_config_name,
                                IdpFieldConfiguration.deleted_at.is_(None)
                            )
                        )).first()

                        if not config:
                            raise ValueError(f"Active field configuration '{effective_config_name}' not found.")

                        # Honor Input Mode: text path when there's a text layer (or explicit text mode),
                        # else read the page images with the vision model (image/scanned docs) — otherwise
                        # all fields come back null.
                        if self._decide_route(text) == "text":
                            extracted = await extract_named_config(
                                session=session,
                                ocr_text=text,
                                field_config_id=config.id,
                                llm_model=self.llm,
                            )
                        else:
                            extracted = await self._extract_via_vision(src, config.id, session)
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
                    from agentcore.services.database.models.idp.config import IdpFieldConfiguration
                    from sqlmodel import select

                    async with session_scope() as session:
                        config = (await session.exec(
                            select(IdpFieldConfiguration)
                            .where(
                                IdpFieldConfiguration.name == effective_config_name,
                                IdpFieldConfiguration.deleted_at.is_(None),
                            )
                        )).first()
                        if not config:
                            raise ValueError(f"Active field configuration '{effective_config_name}' not found.")
                        # Storage-based vision (native docs live in storage, not on a local path).
                        extracted = await self._extract_via_vision(src, config.id, session)
                        raw = json.dumps(extracted, indent=2)

            headers_count = len(extracted.get("headers", {})) if isinstance(extracted, dict) else 0
            line_items_count = len(extracted.get("line_items", [])) if isinstance(extracted, dict) else 0
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

        # Resolve the Field Configuration ONCE (single or classifier-routed), same rules as a single doc.
        config_names: list[str] = list(self.config_names) if self.config_names else []
        single_config = str(getattr(self, "config_name", "") or "").strip()
        if not single_config and not config_names:
            self.status = "No Field Configuration selected."
            return carry(rep, text="", extracted={"error": "No Field Configuration selected."})

        self._override_config_name = None
        if config_names and self.extraction_mode == "field_configuration":
            matched = await self._resolve_config_name_from_classification(rep)
            if matched is None:
                self.status = "Skipped — no field configuration matches the classified type."
                return carry(rep, text="", extracted={})
            self._override_config_name = matched
        effective = self._override_config_name or single_config or (config_names[0] if config_names else None)

        if not self.llm:
            self.status = "No model connected."
            return carry(rep, text="", extracted={"error": "No model connected"})

        from agentcore.services.deps import session_scope
        from agentcore.services.database.models.idp.config import IdpFieldConfiguration
        from agentcore.services.idp import long_doc
        from agentcore.services.idp.extraction import extract_named_config
        from sqlmodel import select

        results: list[dict] = []
        via_vision = False
        try:
            async with session_scope() as session:
                config = (await session.exec(
                    select(IdpFieldConfiguration).where(
                        IdpFieldConfiguration.name == effective,
                        IdpFieldConfiguration.deleted_at.is_(None),
                    )
                )).first()
                if not config:
                    raise ValueError(f"Active field configuration '{effective}' not found.")

                # If NO chunk carries text (a scanned / image document with no OCR text — no OCR node, or
                # PaddleOCR unavailable), read the page IMAGES with the vision model instead of extracting
                # from empty text. Mirrors the single-doc auto path so the same flow works for scanned docs.
                via_vision = not any(((ch.text if isinstance(ch, Message) else str(ch)) or "").strip() for ch in chunks)
                if via_vision:
                    self.status = "No text in chunks — extracting via vision"
                    ex = await self._extract_via_vision(rep, config.id, session)
                    if isinstance(ex, dict) and (ex.get("headers") or ex.get("line_items")):
                        results.append(ex)
                else:
                    for ch in chunks:
                        ctext = ch.text if isinstance(ch, Message) else str(ch)
                        if not (ctext and ctext.strip()):
                            continue
                        try:
                            ex = await extract_named_config(
                                session=session, ocr_text=ctext, field_config_id=config.id, llm_model=self.llm)
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
