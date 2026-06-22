from __future__ import annotations

import logging
from abc import ABC
from abc import abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)


class LLMBackend(ABC):
    """Single-method abstraction over any chat-completion backend.

    Callers pass a complete message history and receive the raw response
    string — JSON parsing, sanitisation, and retry logic stay in LLMPlayer.
    """

    @abstractmethod
    def complete(self, messages: list[dict[str, str]]) -> str: ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Human-readable model identifier for logging and healthz."""
        ...


class OllamaBackend(LLMBackend):
    def __init__(self, settings: Settings) -> None:
        from ollama import Client

        self._client = Client(host=settings.ollama_host)
        self._model = settings.ollama_model
        self._opts = {
            "temperature": settings.ollama_temperature,
            "seed": settings.ollama_seed,
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": settings.ollama_num_predict,
        }

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, messages: list[dict[str, str]]) -> str:
        resp = self._client.chat(
            model=self._model,
            messages=messages,
            format="json",   # loose JSON mode — format=<schema> returns empty on MLX models
            think=False,     # top-level param: prevents thinking tokens eating the budget
            options=self._opts,
        )
        return resp.message.content


class LlamaCppBackend(LLMBackend):
    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            base_url=f"{settings.llama_cpp_host.rstrip('/')}/v1",
            api_key="not-required",
        )
        self._model = settings.llama_cpp_model
        self._temperature = settings.ollama_temperature
        self._max_tokens = settings.ollama_num_predict

    @property
    def model_name(self) -> str:
        return self._model or "llama.cpp"

    def complete(self, messages: list[dict[str, str]]) -> str:
        resp = self._client.chat.completions.create(
            model=self._model or "default",
            messages=messages,  # type: ignore[arg-type]  # llama.cpp accepts plain dicts
            response_format={"type": "json_object"},  # type: ignore[arg-type]
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return resp.choices[0].message.content or ""


def create_backend(settings: Settings) -> LLMBackend:

    if settings.llm_backend == "llama_cpp":
        logger.info("Using llama.cpp backend at %s", settings.llama_cpp_host)
        return LlamaCppBackend(settings)
    logger.info("Using Ollama backend at %s (model: %s)", settings.ollama_host, settings.ollama_model)
    return OllamaBackend(settings)
