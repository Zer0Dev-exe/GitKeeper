"""Clase base de los proveedores y utilidades HTTP compartidas."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterator

import httpx

from gitkeeper import __version__
from gitkeeper.errors import AuthError, NotFoundError, ProviderError, UnsupportedOperation
from gitkeeper.models import Repo

USER_AGENT = f"gitkeeper/{__version__}"
RETRY_STATUS = {429, 502, 503, 504}
README_RE = re.compile(r"^readme(\.[a-z0-9]+)?$", re.IGNORECASE)
MAX_TOPICS = 20


def normalize_topics(topics: list[str]) -> list[str]:
    """Normaliza topics al formato más restrictivo (GitHub): minúsculas, guiones, <=50 caracteres."""
    seen: list[str] = []
    for topic in topics:
        slug = re.sub(r"[\s_.]+", "-", topic.strip().lower())
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        slug = re.sub(r"-{2,}", "-", slug).strip("-")[:50].strip("-")
        if slug and slug not in seen:
            seen.append(slug)
    return seen[:MAX_TOPICS]


def to_percentages(values: dict[str, float]) -> dict[str, float]:
    """Normaliza a porcentajes (sobre 100) ordenados de mayor a menor."""
    total = sum(v for v in values.values() if v > 0)
    if total <= 0:
        return {}
    ranked = sorted(((k, v) for k, v in values.items() if v > 0), key=lambda kv: kv[1], reverse=True)
    return {name: value * 100 / total for name, value in ranked}


def pick_readme(filenames: list[str]) -> str | None:
    """Elige el README preferido de una lista de ficheros del directorio raíz."""
    candidates = [name for name in filenames if README_RE.match(name)]
    if not candidates:
        return None
    preference = [".md", ".markdown", ".rst", ".txt", ""]

    def rank(name: str) -> int:
        ext = name[len("readme"):].lower()
        return preference.index(ext) if ext in preference else len(preference)

    return sorted(candidates, key=rank)[0]


class Provider(ABC):
    """Interfaz común a GitHub, GitLab y Bitbucket."""

    name: str = ""
    label: str = ""
    supports_topics: bool = True
    supports_archive: bool = True
    # Si el listado ya incluye el lenguaje principal (GitLab no lo incluye).
    lists_language: bool = True

    def __init__(
        self,
        token: str,
        api_url: str,
        client: httpx.Client | None = None,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.max_retries = max_retries
        self._sleep = sleep
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True)
        self.client.headers.update({"User-Agent": USER_AGENT, **self.auth_headers()})

    # ------------------------------------------------------------------ HTTP
    @abstractmethod
    def auth_headers(self) -> dict[str, str]: ...

    def url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.api_url}/{path.lstrip('/')}"

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = self.client.request(method, self.url(path), **kwargs)
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    attempt += 1
                    self._sleep(2 ** (attempt - 1))
                    continue
                raise ProviderError(f"{self.label}: error de red ({exc}).") from exc

            if response.status_code in RETRY_STATUS and attempt < self.max_retries:
                attempt += 1
                self._sleep(self._retry_delay(response, attempt))
                continue
            if response.is_success:
                return response
            self._raise_for(response)

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
        return float(2 ** (attempt - 1))

    def _raise_for(self, response: httpx.Response) -> None:
        message = self.error_message(response)
        status = response.status_code
        if status == 401:
            raise AuthError(f"{self.label}: credenciales rechazadas (401). {message}".strip())
        if status == 403:
            raise ProviderError(
                f"{self.label}: permiso denegado (403). Revisa los permisos/scopes del token. {message}".strip(),
                status,
            )
        if status == 404:
            raise NotFoundError(f"{self.label}: no encontrado (404). {message}".strip(), status)
        raise ProviderError(f"{self.label}: error {status}. {message}".strip(), status)

    @staticmethod
    def error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text[:200].strip()
        if isinstance(data, dict):
            for key in ("message", "error_description", "error"):
                value = data.get(key)
                if isinstance(value, dict):
                    value = value.get("message") or value.get("detail")
                if value:
                    return str(value) if not isinstance(value, list) else "; ".join(map(str, value))
        return ""

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[Any]:
        """Recorre páginas siguiendo el header ``Link: rel=next`` (GitHub, GitLab)."""
        url: str | None = path
        current_params = params
        while url:
            response = self.request("GET", url, params=current_params)
            items = response.json()
            yield from items
            next_link = response.links.get("next", {}).get("url")
            if next_link:
                url, current_params = next_link, None
            else:
                url = self.next_page_fallback(response, path, params)
                current_params = None

    def next_page_fallback(
        self, response: httpx.Response, path: str, params: dict[str, Any] | None
    ) -> str | None:
        return None

    def close(self) -> None:
        self.client.close()

    # ----------------------------------------------------------- operaciones
    @abstractmethod
    def whoami(self) -> str:
        """Nombre del usuario autenticado (sirve para validar el token)."""

    @abstractmethod
    def list_repos(self) -> list[Repo]: ...

    @abstractmethod
    def get_repo(self, full_name: str) -> Repo: ...

    @abstractmethod
    def update_description(self, repo: Repo, description: str) -> Repo: ...

    def set_topics(self, repo: Repo, topics: list[str]) -> Repo:
        raise UnsupportedOperation(f"{self.label} no soporta topics.")

    def set_archived(self, repo: Repo, archived: bool) -> Repo:
        raise UnsupportedOperation(f"{self.label} no permite archivar repositorios por API.")

    @abstractmethod
    def delete_repo(self, repo: Repo) -> None: ...

    def get_languages(self, repo: Repo) -> dict[str, float]:
        """Lenguajes y su porcentaje, de mayor a menor. Por defecto solo el principal."""
        return {repo.language: 100.0} if repo.language else {}

    @abstractmethod
    def list_root(self, repo: Repo) -> list[str]:
        """Nombres de ficheros y carpetas del directorio raíz (carpetas terminan en '/')."""

    @abstractmethod
    def read_file(self, repo: Repo, path: str) -> str | None: ...

    def get_readme(self, repo: Repo) -> str | None:
        try:
            names = [n for n in self.list_root(repo) if not n.endswith("/")]
        except NotFoundError:
            return None
        readme = pick_readme(names)
        return self.read_file(repo, readme) if readme else None
