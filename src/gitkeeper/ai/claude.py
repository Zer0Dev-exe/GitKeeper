"""Claude (Anthropic) vía el SDK oficial ``anthropic``."""

from __future__ import annotations

from typing import Any

from gitkeeper.ai.base import AIProvider
from gitkeeper.ai.prompt import SCHEMA
from gitkeeper.errors import AIError


class ClaudeProvider(AIProvider):
    name = "claude"
    label = "Claude"
    default_model = "claude-opus-5"

    def __init__(self, model: str = "", api_key: str = "", client: Any = None):
        super().__init__(model, api_key)
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise AIError("Falta el SDK de Anthropic: pip install anthropic") from exc
            # Sin api_key el SDK usa ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / perfil de `ant auth login`.
            self._client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str) -> str:
        client = self.client
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                # Tarea corta y sencilla: esfuerzo bajo + salida JSON garantizada por esquema.
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                # Si el modelo rechaza la petición, la API la reintenta con el modelo de respaldo recomendado.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except Exception as exc:
            raise AIError(_describe_error(exc)) from exc

        if response.stop_reason == "refusal":
            raise AIError("Claude rechazó generar la descripción para este repositorio.")
        if response.stop_reason == "max_tokens":
            raise AIError("La respuesta de Claude se cortó (max_tokens).")
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise AIError("Claude no devolvió texto.")


def _describe_error(exc: Exception) -> str:
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return f"Claude: {exc}"
    if isinstance(exc, anthropic.AuthenticationError):
        return "Claude: API key inválida (revisa ANTHROPIC_API_KEY o ai.api_key)."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "Claude: la API key no tiene permiso para este modelo."
    if isinstance(exc, anthropic.NotFoundError):
        return "Claude: modelo no encontrado (revisa ai.model)."
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude: límite de peticiones alcanzado, prueba en unos segundos."
    if isinstance(exc, anthropic.APIStatusError):
        return f"Claude: error {exc.status_code} de la API: {exc.message}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "Claude: no se pudo conectar con la API."
    return f"Claude: {exc}"
