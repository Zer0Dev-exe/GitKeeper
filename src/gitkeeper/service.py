"""Lógica de negocio compartida por la CLI y la TUI."""

from __future__ import annotations

import difflib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import urlparse

from gitkeeper.ai.base import AIProvider
from gitkeeper.errors import (
    AuthError,
    GitKeeperError,
    NotFoundError,
    ProviderError,
    RepoResolutionError,
)
from gitkeeper.models import Repo, Suggestion
from gitkeeper.providers.base import Provider

SORT_KEYS = ("updated", "created", "name", "stars")
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class FetchResult:
    repos: list[Repo] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def filter_repos(
    repos: Iterable[Repo],
    *,
    archived: bool | None = None,
    visibility: str | None = None,
    language: str | None = None,
    forks: bool | None = None,
    provider: str | None = None,
    query: str | None = None,
) -> list[Repo]:
    """Filtra repos. ``None`` en un filtro significa "no filtrar por eso"."""
    result = []
    for repo in repos:
        if archived is not None and repo.archived != archived:
            continue
        if visibility and repo.visibility != visibility:
            continue
        if language and (repo.language or "").lower() != language.lower():
            continue
        if forks is not None and repo.fork != forks:
            continue
        if provider and repo.provider != provider:
            continue
        if query and not repo.matches(query):
            continue
        result.append(repo)
    return result


def sort_repos(repos: Iterable[Repo], key: str = "updated", reverse: bool | None = None) -> list[Repo]:
    """Ordena repos. Por defecto: fechas y estrellas descendente, nombre ascendente."""
    if key not in SORT_KEYS:
        raise GitKeeperError(f"Orden inválido '{key}'. Válidos: {', '.join(SORT_KEYS)}.")
    if reverse is None:
        reverse = key != "name"
    getters: dict[str, Callable[[Repo], object]] = {
        "updated": lambda r: r.updated_at or _EPOCH,
        "created": lambda r: r.created_at or _EPOCH,
        "name": lambda r: r.full_name.lower(),
        "stars": lambda r: r.stars,
    }
    return sorted(repos, key=getters[key], reverse=reverse)


class GitKeeper:
    def __init__(
        self,
        providers: list[Provider],
        ai_factory: Callable[[], AIProvider] | None = None,
        language: str = "es",
    ):
        self.providers = {p.name: p for p in providers}
        self._ai_factory = ai_factory
        self._ai: AIProvider | None = None
        self.language = language
        self._cache: FetchResult | None = None
        self._languages: dict[str, dict[str, float]] = {}

    # ------------------------------------------------------------ utilidades
    def require_providers(self) -> None:
        if not self.providers:
            raise AuthError(
                "No hay ninguna cuenta configurada. Ejecuta 'gitkeeper auth login' "
                "o define GITHUB_TOKEN / GITLAB_TOKEN / BITBUCKET_TOKEN."
            )

    def provider_for(self, repo: Repo) -> Provider:
        try:
            return self.providers[repo.provider]
        except KeyError as exc:
            raise AuthError(f"No hay credenciales para {repo.provider}.") from exc

    @property
    def ai(self) -> AIProvider:
        if self._ai is None:
            if self._ai_factory is None:
                raise GitKeeperError("No hay ningún proveedor de IA configurado.")
            self._ai = self._ai_factory()
        return self._ai

    def close(self) -> None:
        for provider in self.providers.values():
            provider.close()

    # --------------------------------------------------------------- listado
    def fetch_all(self, only: list[str] | None = None, refresh: bool = False) -> FetchResult:
        """Descarga los repos de todos los proveedores en paralelo.

        Un fallo en un proveedor no impide ver los demás: se devuelve en ``errors``.
        """
        self.require_providers()
        if self._cache is not None and not refresh and not only:
            return self._cache
        targets = [p for p in self.providers.values() if not only or p.name in only]
        result = FetchResult()
        if not targets:
            return result
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futures = {p.name: pool.submit(p.list_repos) for p in targets}
        for name, future in futures.items():
            try:
                result.repos.extend(future.result())
            except GitKeeperError as exc:
                result.errors[name] = str(exc)
        if not only:
            self._cache = result
        return result

    def invalidate(self) -> None:
        self._cache = None

    def recent(self, limit: int = 10, include_archived: bool = False) -> FetchResult:
        fetched = self.fetch_all()
        repos = fetched.repos if include_archived else filter_repos(fetched.repos, archived=False)
        return FetchResult(sort_repos(repos, "updated")[:limit], fetched.errors)

    def latest(self, limit: int = 10, include_archived: bool = False) -> FetchResult:
        fetched = self.fetch_all()
        repos = fetched.repos if include_archived else filter_repos(fetched.repos, archived=False)
        return FetchResult(sort_repos(repos, "created")[:limit], fetched.errors)

    def search(self, query: str, include_archived: bool = True) -> FetchResult:
        fetched = self.fetch_all()
        repos = filter_repos(fetched.repos, query=query, archived=None if include_archived else False)
        return FetchResult(sort_repos(repos, "updated"), fetched.errors)

    # ------------------------------------------------------------ resolución
    def _provider_from_host(self, host: str) -> str | None:
        host = host.lower()
        aliases = {"github.com": "github", "gitlab.com": "gitlab", "bitbucket.org": "bitbucket"}
        if host in aliases:
            return aliases[host]
        for provider in self.providers.values():
            api_host = (urlparse(provider.api_url).hostname or "").lower()
            if api_host == host or api_host.removeprefix("api.") == host:
                return provider.name
        return None

    def parse_ref(self, ref: str) -> tuple[str | None, str]:
        """Separa ``proveedor:ruta`` o una URL web en (proveedor, ruta)."""
        text = ref.strip()
        if text.startswith(("http://", "https://")):
            parsed = urlparse(text)
            path = parsed.path.strip("/").removesuffix(".git")
            # Descarta sufijos de navegación: /-/tree/..., /tree/main, /src/master...
            path = path.split("/-/")[0]
            provider = self._provider_from_host(parsed.hostname or "")
            if provider in ("github", "bitbucket"):
                path = "/".join(path.split("/")[:2])
            return provider, path
        if ":" in text:
            prefix, rest = text.split(":", 1)
            if prefix.lower() in ("github", "gitlab", "bitbucket", "gh", "gl", "bb"):
                short = {"gh": "github", "gl": "gitlab", "bb": "bitbucket"}
                return short.get(prefix.lower(), prefix.lower()), rest.strip("/")
        return None, text.strip("/")

    def resolve(self, ref: str) -> Repo:
        """Encuentra un repo a partir de ``nombre``, ``owner/nombre``, ``github:owner/nombre`` o URL."""
        self.require_providers()
        provider_name, path = self.parse_ref(ref)
        if not path:
            raise RepoResolutionError("Indica un repositorio.")

        if provider_name:
            if provider_name not in self.providers:
                raise AuthError(f"No hay credenciales para {provider_name}.")
            candidates = [self.providers[provider_name]]
        else:
            candidates = list(self.providers.values())

        if "/" in path:
            if self._cache is not None:
                cached = [
                    r for r in self._cache.repos
                    if r.full_name.lower() == path.lower() and r.provider in {p.name for p in candidates}
                ]
                if len(cached) == 1:
                    return cached[0]
            found = []
            for provider in candidates:
                try:
                    found.append(provider.get_repo(path))
                except NotFoundError:
                    continue
            return self._single(found, ref)

        fetched = self.fetch_all()
        names = {p.name for p in candidates}
        pool = [r for r in fetched.repos if r.provider in names]
        matches = [r for r in pool if r.name.lower() == path.lower()]
        if not matches:
            close = difflib.get_close_matches(path.lower(), [r.name.lower() for r in pool], n=3, cutoff=0.6)
            hint = f" ¿Quizá: {', '.join(close)}?" if close else ""
            raise RepoResolutionError(f"No se encontró el repositorio '{ref}'.{hint}")
        return self._single(matches, ref)

    @staticmethod
    def _single(found: list[Repo], ref: str) -> Repo:
        if not found:
            raise RepoResolutionError(f"No se encontró el repositorio '{ref}'.")
        if len(found) > 1:
            options = ", ".join(r.ref for r in found)
            raise RepoResolutionError(f"'{ref}' es ambiguo: {options}. Usa la referencia completa.")
        return found[0]

    # ------------------------------------------------------------ operaciones
    def _replace_cached(self, updated: Repo) -> None:
        if self._cache is None:
            return
        for i, repo in enumerate(self._cache.repos):
            if repo.provider == updated.provider and repo.id == updated.id:
                self._cache.repos[i] = updated
                return

    def cached_languages(self, repo: Repo) -> dict[str, float] | None:
        return self._languages.get(repo.ref)

    def languages(self, repo: Repo) -> dict[str, float]:
        """Lenguajes del repo con su porcentaje (se consulta una sola vez por repo).

        Si la consulta falla (permisos, red...) se guarda vacío y se usa el lenguaje principal.
        """
        if repo.ref not in self._languages:
            try:
                self._languages[repo.ref] = self.provider_for(repo).get_languages(repo)
            except GitKeeperError:
                self._languages[repo.ref] = {}
        return self._languages[repo.ref]

    def suggest(self, repo: Repo) -> Suggestion:
        """Pide a la IA una descripción.

        Si el token no puede leer el contenido del repo (403), se genera igualmente con el
        nombre, los lenguajes y el resto de metadatos, y se avisa en ``Suggestion.warning``.
        """
        provider = self.provider_for(repo)
        no_access = False
        try:
            readme = provider.get_readme(repo)
        except ProviderError as exc:
            if exc.status_code != 403:
                raise
            readme, no_access = None, True
        try:
            files = [] if no_access else provider.list_root(repo)
        except NotFoundError:
            files = []  # repositorio vacío
        except ProviderError as exc:
            if exc.status_code != 403:
                raise
            files, no_access = [], True
        languages = self.languages(repo)
        suggestion = self.ai.suggest(repo, readme, files, self.language, languages)
        if no_access:
            suggestion.warning = (
                "Sin permiso para leer el README ni los ficheros: la propuesta se basa solo en el "
                "nombre, los lenguajes y los metadatos. Revísala con cuidado."
            )
        return suggestion

    def update_description(self, repo: Repo, description: str, topics: list[str] | None = None) -> Repo:
        provider = self.provider_for(repo)
        updated = provider.update_description(repo, description.strip())
        # Los topics se ignoran en proveedores que no los soportan (Bitbucket).
        if topics and provider.supports_topics:
            updated = provider.set_topics(updated, topics)
        self._replace_cached(updated)
        return updated

    def set_archived(self, repo: Repo, archived: bool) -> Repo:
        updated = self.provider_for(repo).set_archived(repo, archived)
        self._replace_cached(updated)
        return updated

    def delete(self, repo: Repo) -> None:
        self.provider_for(repo).delete_repo(repo)
        if self._cache is not None:
            self._cache.repos = [
                r for r in self._cache.repos if not (r.provider == repo.provider and r.id == repo.id)
            ]
