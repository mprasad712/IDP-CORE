from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import DataInput, DropdownInput, HandleInput, MessageTextInput, MultilineInput, Output
from agentcore.schema.data import Data
from agentcore.schema.message import Message


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
            # Extraction in the builder is config-driven: pick a saved Field Configuration
            # (text or vision). The freeform dynamic-prompt mode is intentionally NOT offered
            # here — author fields on the Field Configurations page instead. The backend still
            # supports dynamic_prompt at runtime (legacy agents + config generation), so this
            # only hides it from the canvas, it does not remove the capability.
            options=["field_configuration", "multimodal_config"],
            value="field_configuration",
            info="field_configuration: extract using a saved text schema. multimodal_config: extract using a saved schema with a vision model.",
        ),
        MultilineInput(
            name="prompt",
            display_name="Extraction Prompt",
            value="",
            advanced=True,
            info="Legacy/advanced: freeform prompt used only by the backend dynamic_prompt path. "
            "Not used by the config-driven builder modes.",
        ),
        MessageTextInput(
            name="config_name",
            display_name="Field Configuration Name",
            value="",
            info="Name of the saved Field Configuration from the Configuration page. Used when Extraction Mode is 'field_configuration' or 'multimodal_config'.",
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
        if field_name == "extraction_mode":
            is_prompt = field_value in ("dynamic_prompt", "multimodal_prompt")
            build_config["prompt"]["show"] = is_prompt
            build_config["config_name"]["show"] = not is_prompt

            if not is_prompt:
                build_config["config_name"]["options"] = self._fetch_config_names()

        if field_name == "config_name":
            # Refresh options whenever this field is touched (e.g. on initial render)
            current_options = self._fetch_config_names()
            build_config["config_name"]["options"] = current_options

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

    def _resolve_document_path(self, src: Any) -> Path | None:
        if not src:
            return None

        # Check attributes / dict fields
        candidates: list[str] = []

        if isinstance(src, str):
            candidates.append(src)
        elif isinstance(src, dict):
            for key in ("file_path", "path", "file", "text", "source"):
                val = src.get(key)
                if val and isinstance(val, str):
                    candidates.append(val)
        else:
            # Check message files list
            files = getattr(src, "files", None)
            if files and isinstance(files, list):
                for f in files:
                    if isinstance(f, str):
                        candidates.append(f)
                    elif hasattr(f, "path"):
                        candidates.append(str(f.path))
            
            # Check .data dict
            data_dict = getattr(src, "data", None)
            if data_dict and isinstance(data_dict, dict):
                for key in ("file_path", "path", "file", "text", "source"):
                    val = data_dict.get(key)
                    if val and isinstance(val, str) and val.strip():
                        candidates.append(val.strip())

            # Check .text attribute
            text_val = getattr(src, "text", None)
            if text_val and isinstance(text_val, str) and text_val.strip():
                # Only treat text_val as path if it looks like a path/file
                t_val = text_val.strip()
                if any(t_val.lower().endswith(ext) for ext in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp", ".xlsx", ".xls", ".docx"]):
                    candidates.append(t_val)

            # Check .path attribute
            path_val = getattr(src, "path", None)
            if path_val is not None:
                path_str = str(path_val).strip()
                if path_str and path_str != "None":
                    candidates.append(path_str)

        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate or candidate == "None":
                continue
            try:
                p = Path(candidate)
                if p.exists() and p.is_file():
                    return p
            except Exception:
                pass
        return None

    # ── extraction ────────────────────────────────────────────────────────────

    async def extract(self) -> Data:
        src = self.document
        text = src.text if isinstance(src, Message) else str(src)

        # Config-based modes build their own prompt inside extract_named_config / extract_multimodal
        # using the general template + DB field prompts — no pre-build needed here.
        if self.extraction_mode not in ("field_configuration", "multimodal_config"):
            prompt = (self.prompt or "").strip()
            if not prompt:
                prompt = "Extract all key fields from this document. Return as structured JSON."
        else:
            prompt = ""  # unused in config modes

        try:
            if self.extraction_mode == "dynamic_prompt":
                if not self.llm:
                    raw = f"[No model connected — prompt would be: {prompt[:200]}...]"
                    extracted = {"error": "No model connected"}
                else:
                    from agentcore.services.idp.extraction import extract_dynamic
                    extracted = await extract_dynamic(ocr_text=text, prompt=prompt, llm_model=self.llm)
                    raw = json.dumps(extracted, indent=2)

            elif self.extraction_mode == "field_configuration":
                if not self.llm:
                    raw = f"[No model connected — config would be: {self.config_name}]"
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
                                IdpFieldConfiguration.name == self.config_name,
                                IdpFieldConfiguration.deleted_at.is_(None)
                            )
                        )).first()

                        if not config:
                            raise ValueError(f"Active field configuration '{self.config_name}' not found.")

                        extracted = await extract_named_config(
                            session=session,
                            ocr_text=text,
                            field_config_id=config.id,
                            llm_model=self.llm
                        )
                        raw = json.dumps(extracted, indent=2)

            elif self.extraction_mode in ("multimodal_prompt", "multimodal_config"):
                file_path = self._resolve_document_path(src)
                if not file_path:
                    raise ValueError("Could not resolve document file path for multimodal extraction.")

                if not self.llm:
                    raw = f"[No model connected — multimodal prompt/config would be processed]"
                    extracted = {"error": "No model connected"}
                else:
                    from agentcore.services.idp.extraction import extract_multimodal

                    if self.extraction_mode == "multimodal_prompt":
                        extracted = await extract_multimodal(
                            file_path=file_path,
                            prompt_or_config_id=prompt,
                            llm_model=self.llm
                        )
                        raw = json.dumps(extracted, indent=2)
                    else:
                        from agentcore.services.deps import session_scope
                        from agentcore.services.database.models.idp.config import IdpFieldConfiguration
                        from sqlmodel import select

                        async with session_scope() as session:
                            config = (await session.exec(
                                select(IdpFieldConfiguration)
                                .where(
                                    IdpFieldConfiguration.name == self.config_name,
                                    IdpFieldConfiguration.deleted_at.is_(None)
                                )
                            )).first()

                            if not config:
                                raise ValueError(f"Active field configuration '{self.config_name}' not found.")

                            extracted = await extract_multimodal(
                                file_path=file_path,
                                prompt_or_config_id=config.id,
                                llm_model=self.llm,
                                session=session
                            )
                            raw = json.dumps(extracted, indent=2)

            headers_count = len(extracted.get("headers", {})) if isinstance(extracted, dict) else 0
            line_items_count = len(extracted.get("line_items", [])) if isinstance(extracted, dict) else 0
            self.status = f"Extracted {headers_count} header(s) and {line_items_count} line item(s)"
            return Data(data=extracted, text=raw)
        except Exception as exc:
            self.status = f"Error: {exc}"
            logger.error(f"[AIFieldExtractor] Extraction failed: {exc}")
            return Data(data={"error": str(exc)}, text="")

    def _build_config_prompt(self, config_name: str) -> str:
        try:
            from sqlmodel import Session, select
            from agentcore.services.deps import get_service
            from agentcore.services.schema import ServiceType
            from agentcore.services.database.models.idp.config import (
                IdpFieldConfiguration, IdpFieldConfigHeader
            )

            db_service = get_service(ServiceType.DATABASE_SERVICE)
            sync_engine = db_service.engine.sync_engine
            with Session(sync_engine) as session:
                config = session.exec(
                    select(IdpFieldConfiguration)
                    .where(
                        IdpFieldConfiguration.name == config_name,
                        IdpFieldConfiguration.deleted_at.is_(None),
                    )
                ).first()
                if not config:
                    return f"Extract all key fields from this document as JSON."

                headers = session.exec(
                    select(IdpFieldConfigHeader)
                    .where(IdpFieldConfigHeader.config_id == config.id)
                ).all()

                if not headers:
                    return f"Extract all key fields from this document as JSON."

                field_list = ", ".join(h.field_name for h in headers)
                return (
                    f"Extract the following fields from this document and return as structured JSON: {field_list}. "
                    f"For each field include its value and a confidence score (0–1)."
                )
        except Exception as exc:
            logger.warning(f"[AIFieldExtractor] Config prompt build failed: {exc}")
            return "Extract all key fields from this document as JSON."

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
