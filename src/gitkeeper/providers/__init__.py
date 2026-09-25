"""Registro de proveedores y construcción a partir de la configuración."""

from __future__ import annotations

from typing import Any

from gitkeeper.auth import resolve_credentials
from gitkeeper.config import PROVIDERS, Config
from gitkeeper.providers.base import Provider
from gitkeeper.providers.bitbucket import BitbucketProvider
from gitkeeper.providers.github import GitHubProvider
from gitkeeper.providers.gitlab import GitLabProvider

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "github": GitHubProvider,
    "gitlab": GitLabProvider,
    "bitbucket": BitbucketProvider,
}

LABELS = {name: cls.label for name, cls in PROVIDER_CLASSES.items()}


def build_provider(name: str, config: Config, token: str, username: str = "", **kwargs: Any) -> Provider:
    section = config.section(name)
    options: dict[str, Any] = {"api_url": section.get("api_url") or None}
    if name == "github":
        options["affiliation"] = section.get("affiliation", "")
    elif name == "gitlab":
        options["scope"] = section.get("scope", "")
    elif name == "bitbucket":
        options["username"] = username
        options["workspace"] = section.get("workspace", "")
    options = {k: v for k, v in options.items() if v is not None}
    return PROVIDER_CLASSES[name](token, **options, **kwargs)


def build_providers(config: Config, only: list[str] | None = None) -> list[Provider]:
    """Instancia los proveedores que tienen credenciales configuradas."""
    providers = []
    for name in PROVIDERS:
        if only and name not in only:
            continue
        creds = resolve_credentials(name, config)
        if creds:
            providers.append(build_provider(name, config, creds.token, creds.username))
    return providers


__all__ = [
    "PROVIDER_CLASSES",
    "LABELS",
    "Provider",
    "GitHubProvider",
    "GitLabProvider",
    "BitbucketProvider",
    "build_provider",
    "build_providers",
]
