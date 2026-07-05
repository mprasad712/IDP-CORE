import logging
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from app.providers.base import BaseProvider, register_provider

logger = logging.getLogger(__name__)

_GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


@register_provider("google")
class GoogleProvider(BaseProvider):
    """Google Generative AI (Gemini) provider."""

    async def list_models(self, provider_config: dict[str, Any]) -> list[str]:
        api_key = provider_config.get("api_key", "")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    _GOOGLE_MODELS_URL,
                    params={"key": api_key},
                )
                response.raise_for_status()
                data = response.json()
                models = []
                for model in data.get("models", []):
                    if "generateContent" in model.get("supportedGenerationMethods", []):
                        name = model.get("name", "").replace("models/", "")
                        if name:
                            models.append(name)
                models.sort(reverse=True)
                return models
        except Exception as e:
            logger.warning("Failed to list Google models: %s", e)
            return []

    def build_model(
        self,
        model: str,
        provider_config: dict[str, Any],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        n: int | None = None,
        streaming: bool = False,
        seed: int | None = None,
        json_mode: bool = False,
        model_kwargs: dict[str, Any] | None = None,
    ) -> BaseChatModel:
        api_key = provider_config.get("api_key", "")
        # This is the Google AI Studio (Gemini Developer API) provider. Google now issues standard
        # AI Studio keys that START WITH "AQ.", so the key prefix is NOT a reliable Vertex signal —
        # only route to Vertex when the caller explicitly asks. (Use the dedicated google_vertex
        # provider for real Vertex access with a service account / Vertex Express key.)
        use_vertexai = bool(provider_config.get("use_vertexai"))

        kwargs: dict[str, Any] = {
            "model": model,
            "google_api_key": api_key,
            "streaming": streaming,
        }
        # Always set this explicitly. GOOGLE_GENAI_USE_VERTEXAI is process-global, so leaving it
        # unset after a previous Vertex call would stick every later Google request on Vertex.
        import os
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true" if use_vertexai else "false"

        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens:
            kwargs["max_output_tokens"] = max_tokens
        if top_p is not None:
            kwargs["top_p"] = top_p
        if top_k is not None:
            kwargs["top_k"] = top_k
        if n is not None:
            kwargs["n"] = n
        if model_kwargs:
            cleaned = dict(model_kwargs)
            # Gemini 2.5+/3.x supports visible thinking via thinking_config
            thinking_config = cleaned.pop("thinking_config", None)
            cleaned.pop("thinking", None)  # Anthropic-specific, ignore
            if thinking_config:
                # langchain-google-genai accepts thinking_config at top level
                kwargs["thinking_config"] = thinking_config
            if cleaned:
                kwargs["model_kwargs"] = cleaned

        return ChatGoogleGenerativeAI(**kwargs)

    def build_embeddings(
        self,
        model: str,
        provider_config: dict[str, Any],
        *,
        dimensions: int | None = None,
    ) -> GoogleGenerativeAIEmbeddings:
        api_key = provider_config.get("api_key", "")

        kwargs: dict[str, Any] = {
            "model": model,
            "google_api_key": api_key,
        }
        if dimensions:
            kwargs["output_dimensionality"] = dimensions
        return GoogleGenerativeAIEmbeddings(**kwargs)
