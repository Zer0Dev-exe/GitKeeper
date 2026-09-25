"""OpenAI y APIs compatibles (Ollama, LM Studio, OpenRouter, Azure...) vía el SDK ``openai``."""

from __future__ import annotations

from typing import Any

from gitkeeper.ai.base import AIProvider
from gitkeeper.errors import AIError


class OpenAIProvider(AIProvider):
    name = "openai"
    label = "OpenAI"
    default_model = "gpt-5-mini"

    def __init__(self, model: str = "", api_key: str = "", base_url: str = "", client: Any = None):
        super().__init__(model, api_key)
        self.base_url = base_url
        self._client = client
        if base_url and not model:
            raise AIError("Con ai.base_url (API compatible) debes indicar el modelo en ai.model.")

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import openai
            except ImportError as exc:
                raise AIError("Falta el SDK de OpenAI: pip install openai") from exc
            kwargs: dict[str, Any] = {}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            elif self.base_url:
                # Servidores locales (Ollama...) no necesitan clave, pero el SDK exige una.
                kwargs["api_key"] = "not-needed"
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def describe(self) -> str:
        where = f" @ {self.base_url}" if self.base_url else ""
        return f"{self.label} ({self.model}{where})"

    def complete(self, system: str, user: str) -> str:
        client = self.client
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status == 401:
                raise AIError("OpenAI: API key inválida (revisa OPENAI_API_KEY o ai.api_key).") from exc
            if status == 404:
                raise AIError("OpenAI: modelo no encontrado (revisa ai.model).") from exc
            if status == 429:
                raise AIError("OpenAI: límite de peticiones o cuota agotada.") from exc
            raise AIError(f"OpenAI: {exc}") from exc
        if not response.choices:
            raise AIError("OpenAI no devolvió ninguna respuesta.")
        text = response.choices[0].message.content
        if not text:
            raise AIError("OpenAI devolvió una respuesta vacía.")
        return text
