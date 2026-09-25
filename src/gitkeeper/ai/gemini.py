"""Gemini (Google) vía el SDK oficial ``google-genai``."""

from __future__ import annotations

from typing import Any

from gitkeeper.ai.base import AIProvider
from gitkeeper.ai.prompt import SCHEMA
from gitkeeper.errors import AIError


class GeminiProvider(AIProvider):
    name = "gemini"
    label = "Gemini"
    # Alias que Google mantiene apuntando al último modelo Flash estable.
    default_model = "gemini-flash-latest"

    def __init__(self, model: str = "", api_key: str = "", client: Any = None):
        super().__init__(model, api_key)
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise AIError("Falta el SDK de Gemini: pip install google-genai") from exc
            # Sin api_key el SDK usa GEMINI_API_KEY / GOOGLE_API_KEY.
            self._client = genai.Client(api_key=self.api_key) if self.api_key else genai.Client()
        return self._client

    def complete(self, system: str, user: str) -> str:
        client = self.client
        config = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "response_json_schema": SCHEMA,
        }
        try:
            response = client.models.generate_content(model=self.model, contents=user, config=config)
        except Exception as exc:
            raise AIError(f"Gemini: {_error_text(exc)}") from exc
        text = getattr(response, "text", None)
        if not text:
            raise AIError("Gemini no devolvió texto (posible bloqueo por filtros de seguridad).")
        return text


def _error_text(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code in (401, 403):
        return "API key inválida o sin permisos (revisa GEMINI_API_KEY o ai.api_key)."
    if code == 404:
        return "modelo no encontrado (revisa ai.model)."
    if code == 429:
        return "límite de peticiones alcanzado, prueba en unos segundos."
    return str(exc)
