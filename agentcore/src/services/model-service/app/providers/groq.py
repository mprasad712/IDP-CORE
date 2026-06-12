import logging
import re
from typing import Any

import httpx
from groq import BadRequestError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_groq import ChatGroq

from app.providers.base import BaseProvider, register_provider

logger = logging.getLogger(__name__)

_AUDIO_KEYWORDS = ["whisper", "tts", "speech", "audio"]


@register_provider("groq")
class GroqProvider(BaseProvider):
    """Groq provider. Does not support embeddings."""

    async def list_models(self, provider_config: dict[str, Any]) -> list[str]:
        api_key = provider_config.get("api_key", "")
        base_url = provider_config.get("base_url", "https://api.groq.com")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{base_url}/openai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                data = response.json()
                return [
                    m["id"]
                    for m in data.get("data", [])
                    if not any(kw in m.get("id", "").lower() for kw in _AUDIO_KEYWORDS)
                ]
        except Exception as e:
            logger.warning("Failed to list Groq models: %s", e)
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
        base_url = provider_config.get("base_url", "https://api.groq.com")

        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "streaming": streaming,
        }

        if base_url:
            kwargs["base_url"] = base_url
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if n is not None:
            kwargs["n"] = n
        if model_kwargs:
            # Groq's OpenAI-compatible API does NOT accept Anthropic's
            # `thinking` / `thinking_config` kwargs. Reasoning models like
            # deepseek-r1 / qwen-qwq think automatically — no param needed.
            cleaned = {
                k: v for k, v in model_kwargs.items()
                if k not in ("thinking", "thinking_config")
            }
            if cleaned:
                kwargs["model_kwargs"] = cleaned

        return ChatGroq(**kwargs)

    async def invoke(self, model: BaseChatModel, messages: list[BaseMessage]) -> AIMessage:
        try:
            return await model.ainvoke(messages)
        except BadRequestError as e:
            # Some Groq models emit <function=Name>{...}</function> instead of a
            # proper JSON tool-call object; Groq's API rejects this as tool_use_failed
            # even though the generated JSON is complete and valid.  Extract the JSON
            # and return it as plain content so callers can still parse the result.
            body = e.body if isinstance(e.body, dict) else {}
            error_info = body.get("error", {})
            if error_info.get("code") == "tool_use_failed":
                failed_gen = error_info.get("failed_generation", "")
                match = re.search(r"<function=\w+>(.*?)</function>", failed_gen, re.DOTALL)
                if match:
                    logger.warning(
                        "Groq tool_use_failed: returning extracted JSON from failed_generation as content"
                    )
                    return AIMessage(content=match.group(1).strip())
            raise
