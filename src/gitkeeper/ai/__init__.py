"""Selección del proveedor de IA.

Dos formas de usar la IA:

- **Suscripción** (sin coste extra): a través de las CLIs oficiales ya logueadas —
  Claude Code (Claude Pro/Max), Codex (ChatGPT Plus/Pro) o Gemini CLI (cuenta de Google).
- **API key** (pago por uso): Claude, Gemini, OpenAI o cualquier API compatible con OpenAI.

Con ``ai.provider = "auto"`` (por defecto) se elige en este orden:

1. Lo configurado explícitamente para GitKeeper: ``ai.base_url`` o ``ai.api_key``.
2. Una suscripción: la primera CLI instalada entre Claude Code, Codex y Gemini CLI.
3. Una API key en variables de entorno: Claude, Gemini, OpenAI.
"""

from __future__ import annotations

import os

from gitkeeper.ai.base import AIProvider
from gitkeeper.ai.claude import ClaudeProvider
from gitkeeper.ai.cli_agents import (
    CLI_PROVIDERS,
    ClaudeCodeProvider,
    CLIProvider,
    CodexProvider,
    GeminiCLIProvider,
)
from gitkeeper.ai.gemini import GeminiProvider
from gitkeeper.ai.openai_compat import OpenAIProvider
from gitkeeper.config import Config
from gitkeeper.errors import AIError

ENV_KEYS: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
}


def _guess_from_key(key: str) -> str | None:
    if key.startswith("sk-ant-"):
        return "claude"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("sk-"):
        return "openai"
    return None


def build_cli_provider(name: str, config: Config) -> CLIProvider:
    return CLI_PROVIDERS[name](
        model=str(config.get("ai", "model", "") or ""),
        path=str(config.get("ai", "cli_path", "") or ""),
    )


def detect_provider(config: Config) -> str | None:
    """Nombre del proveedor de IA a usar, o ``None`` si no hay ninguno disponible."""
    chosen = str(config.get("ai", "provider", "auto") or "auto")
    if chosen == "none":
        return None
    if chosen != "auto":
        return chosen
    if config.get("ai", "base_url"):
        return "openai"
    api_key = str(config.get("ai", "api_key", "") or "")
    if api_key:
        guessed = _guess_from_key(api_key)
        if guessed:
            return guessed
    for name in CLI_PROVIDERS:
        # ai.cli_path solo tiene sentido con un proveedor CLI elegido explícitamente.
        if CLI_PROVIDERS[name]().is_available():
            return name
    for name, env_names in ENV_KEYS.items():
        if any(os.environ.get(env) for env in env_names):
            return name
    return None


def get_ai_provider(config: Config) -> AIProvider:
    name = detect_provider(config)
    if name is None:
        raise AIError(
            "No hay ninguna IA configurada. Con suscripción: instala e inicia sesión en Claude Code, "
            "Codex o Gemini CLI. Con API key: define ANTHROPIC_API_KEY, GEMINI_API_KEY u "
            "OPENAI_API_KEY, o ejecuta 'gitkeeper config set ai.api_key <clave>'."
        )
    if name in CLI_PROVIDERS:
        return build_cli_provider(name, config)
    model = str(config.get("ai", "model", "") or "")
    api_key = str(config.get("ai", "api_key", "") or "")
    if name == "claude":
        return ClaudeProvider(model=model, api_key=api_key)
    if name == "gemini":
        return GeminiProvider(model=model, api_key=api_key)
    if name == "openai":
        return OpenAIProvider(model=model, api_key=api_key, base_url=str(config.get("ai", "base_url", "") or ""))
    raise AIError(f"Proveedor de IA desconocido: {name}")


__all__ = [
    "AIProvider",
    "ClaudeProvider",
    "GeminiProvider",
    "OpenAIProvider",
    "ClaudeCodeProvider",
    "CodexProvider",
    "GeminiCLIProvider",
    "CLIProvider",
    "detect_provider",
    "get_ai_provider",
]
