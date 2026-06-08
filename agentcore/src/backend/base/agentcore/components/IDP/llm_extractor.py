from __future__ import annotations

import json

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
            options=["dynamic_prompt", "field_configuration"],
            value="dynamic_prompt",
            info="dynamic_prompt: write a freeform extraction prompt. field_configuration: select a saved schema.",
        ),
        MultilineInput(
            name="prompt",
            display_name="Extraction Prompt",
            value="",
            info="Describe the fields to extract. Used when Extraction Mode is 'dynamic_prompt'.",
        ),
        MessageTextInput(
            name="config_name",
            display_name="Field Configuration Name",
            value="",
            info="Name of the saved Field Configuration from the Configuration page. Used when Extraction Mode is 'field_configuration'.",
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
            is_prompt = field_value == "dynamic_prompt"
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

    # ── extraction ────────────────────────────────────────────────────────────

    async def extract(self) -> Data:
        src = self.document
        text = src.text if isinstance(src, Message) else str(src)

        if self.extraction_mode == "field_configuration" and self.config_name:
            prompt = self._build_config_prompt(self.config_name)
        else:
            prompt = (self.prompt or "").strip()
            if not prompt:
                prompt = "Extract all key fields from this document. Return as structured JSON."

        try:
            if self.extraction_mode == "dynamic_prompt":
                if not self.llm:
                    raw = f"[No model connected — prompt would be: {prompt[:200]}...]"
                    extracted = {"error": "No model connected"}
                else:
                    from agentcore.services.idp.extraction import extract_dynamic
                    extracted = await extract_dynamic(ocr_text=text, prompt=prompt, llm_model=self.llm)
                    raw = json.dumps(extracted, indent=2)
            else:
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
