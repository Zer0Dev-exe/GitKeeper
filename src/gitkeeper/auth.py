"""Resolución de credenciales de cada proveedor.

Orden de prioridad (gana el primero que exista):
1. Variables de entorno (``GITKEEPER_<PROV>_TOKEN`` y las habituales de cada plataforma).
2. Fichero de configuración (lo que guarda ``gitkeeper auth login``).
3. CLI oficial ya autenticada (``gh auth token`` para GitHub).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from gitkeeper.config import Config

TOKEN_ENV_VARS: dict[str, tuple[str, ...]] = {
    "github": ("GITKEEPER_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"),
    "gitlab": ("GITKEEPER_GITLAB_TOKEN", "GITLAB_TOKEN"),
    "bitbucket": ("GITKEEPER_BITBUCKET_TOKEN", "BITBUCKET_TOKEN"),
}

USERNAME_ENV_VARS: dict[str, tuple[str, ...]] = {
    "bitbucket": ("GITKEEPER_BITBUCKET_USERNAME", "BITBUCKET_USERNAME", "BITBUCKET_EMAIL"),
}

# Páginas donde crear los tokens, con los permisos necesarios.
TOKEN_HELP: dict[str, str] = {
    "github": (
        "Crea un token en https://github.com/settings/tokens (classic: scopes 'repo' y "
        "'delete_repo'; fine-grained: permiso 'Administration: read and write' y "
        "'Contents: read')."
    ),
    "gitlab": (
        "Crea un Personal Access Token en https://gitlab.com/-/user_settings/personal_access_tokens "
        "con el scope 'api' (o en la misma ruta de tu instancia self-hosted)."
    ),
    "bitbucket": (
        "Crea un API token en https://id.atlassian.com/manage-profile/security/api-tokens "
        "(scopes de repositorio: read, write, admin, delete) y usa tu email de Atlassian como "
        "usuario. También sirven los access tokens de workspace/repositorio (sin usuario)."
    ),
}


@dataclass
class Credentials:
    provider: str
    token: str
    source: str
    username: str = ""


def _first_env(names: tuple[str, ...]) -> tuple[str, str] | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    return None


def _gh_cli_token() -> str | None:
    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def resolve_credentials(provider: str, config: Config, use_cli: bool = True) -> Credentials | None:
    """Devuelve las credenciales del proveedor o ``None`` si no hay ninguna."""
    username = ""
    if provider in USERNAME_ENV_VARS:
        env_user = _first_env(USERNAME_ENV_VARS[provider])
        username = env_user[1] if env_user else str(config.get(provider, "username", "") or "")

    env = _first_env(TOKEN_ENV_VARS.get(provider, ()))
    if env:
        return Credentials(provider, env[1], f"env:{env[0]}", username)

    token = str(config.get(provider, "token", "") or "").strip()
    if token:
        return Credentials(provider, token, "config", username)

    if use_cli and provider == "github":
        cli_token = _gh_cli_token()
        if cli_token:
            return Credentials(provider, cli_token, "gh cli", username)
    return None
