"""Carga y guardado de la configuración (TOML).

Ruta por defecto: ``<user_config_dir>/gitkeeper/config.toml`` (en Windows
``%LOCALAPPDATA%\\gitkeeper``). Se puede cambiar con la variable ``GITKEEPER_CONFIG``.
"""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from platformdirs import user_config_dir

from gitkeeper.errors import ConfigError

PROVIDERS = ("github", "gitlab", "bitbucket")
AI_PROVIDERS = ("auto", "claude", "gemini", "openai", "claude-code", "codex", "gemini-cli", "none")

DEFAULTS: dict[str, dict[str, Any]] = {
    "general": {
        # Idioma en el que la IA redacta las descripciones.
        "language": "es",
    },
    "github": {
        "token": "",
        "api_url": "https://api.github.com",
        # Qué repos listar: owner, collaborator, organization_member (separados por comas).
        "affiliation": "owner,organization_member",
    },
    "gitlab": {
        "token": "",
        "api_url": "https://gitlab.com/api/v4",
        # "owned" = solo proyectos propios; "member" = todos en los que eres miembro.
        "scope": "member",
    },
    "bitbucket": {
        "token": "",
        # Email de Atlassian (API tokens) o usuario (app passwords). Vacío => Bearer token.
        "username": "",
        # Limita el listado a un workspace concreto (opcional).
        "workspace": "",
        "api_url": "https://api.bitbucket.org/2.0",
    },
    "ai": {
        "provider": "auto",
        "model": "",
        "api_key": "",
        # Solo para 'openai': permite usar APIs compatibles (Ollama, LM Studio, OpenRouter...).
        "base_url": "",
        # Solo para claude-code / codex / gemini-cli: ruta del ejecutable si no está en el PATH.
        "cli_path": "",
    },
}

SECRET_KEYS = {"token", "api_key"}


def default_config_path() -> Path:
    env = os.environ.get("GITKEEPER_CONFIG")
    if env:
        return Path(env)
    return Path(user_config_dir("gitkeeper", appauthor=False)) / "config.toml"


def _parse_value(section: str, key: str, raw: str) -> Any:
    value = raw.strip()
    if section == "ai" and key == "provider" and value not in AI_PROVIDERS:
        raise ConfigError(f"Proveedor de IA inválido '{value}'. Válidos: {', '.join(AI_PROVIDERS)}.")
    if key == "api_url" and value and not value.startswith(("http://", "https://")):
        raise ConfigError(f"'{section}.{key}' debe empezar por http:// o https://.")
    return value


class Config:
    def __init__(self, data: dict[str, Any] | None = None, path: Path | None = None):
        self.path = path or default_config_path()
        self.data: dict[str, dict[str, Any]] = copy.deepcopy(DEFAULTS)
        for section, values in (data or {}).items():
            if isinstance(values, dict):
                self.data.setdefault(section, {}).update(values)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or default_config_path()
        if not path.exists():
            return cls(path=path)
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"No se pudo leer la configuración en {path}: {exc}") from exc
        return cls(data, path=path)

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb") as fh:
            tomli_w.dump(self.data, fh)
        try:
            # Contiene tokens: solo legible por el usuario (no-op efectivo en Windows).
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return self.path

    def get(self, section: str, key: str, default: Any = None) -> Any:
        value = self.data.get(section, {}).get(key, default)
        return default if value is None else value

    def set(self, dotted_key: str, value: str) -> None:
        section, key = self._split(dotted_key)
        self.data.setdefault(section, {})[key] = _parse_value(section, key, value)

    def unset(self, dotted_key: str) -> None:
        section, key = self._split(dotted_key)
        default = DEFAULTS.get(section, {}).get(key)
        if default is None:
            self.data.get(section, {}).pop(key, None)
        else:
            self.data[section][key] = copy.deepcopy(default)

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.data.get(name, {}))

    def redacted(self) -> dict[str, dict[str, Any]]:
        """Copia de la configuración con los secretos enmascarados."""
        out = copy.deepcopy(self.data)
        for values in out.values():
            for key, value in values.items():
                if key in SECRET_KEYS and value:
                    values[key] = mask_secret(str(value))
        return out

    @staticmethod
    def _split(dotted_key: str) -> tuple[str, str]:
        parts = dotted_key.split(".")
        if len(parts) != 2 or not all(parts):
            raise ConfigError("Usa el formato 'seccion.clave', p. ej. 'ai.provider'.")
        section, key = parts
        if section not in DEFAULTS:
            raise ConfigError(f"Sección desconocida '{section}'. Válidas: {', '.join(DEFAULTS)}.")
        if key not in DEFAULTS[section]:
            raise ConfigError(
                f"Clave desconocida '{key}' en '{section}'. Válidas: {', '.join(DEFAULTS[section])}."
            )
        return section, key


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"
