"""Modelo de datos común para los repositorios de cualquier proveedor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def parse_datetime(value: str | None) -> datetime | None:
    """Convierte un timestamp ISO-8601 (con 'Z' u offset) a datetime con zona horaria."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class Repo:
    """Repositorio normalizado.

    ``full_name`` es la ruta dentro del proveedor (``owner/name`` en GitHub y
    Bitbucket, ``group/subgroup/name`` en GitLab). ``id`` es el identificador que
    usa la API del proveedor para operar sobre él.
    """

    provider: str
    id: str
    full_name: str
    name: str
    owner: str
    description: str = ""
    url: str = ""
    # "public", "private" o "internal" (GitLab).
    visibility: str = "public"
    archived: bool = False
    fork: bool = False
    language: str | None = None
    topics: list[str] = field(default_factory=list)
    default_branch: str | None = None
    stars: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def ref(self) -> str:
        """Referencia única entre proveedores: ``github:owner/name``."""
        return f"{self.provider}:{self.full_name}"

    @property
    def private(self) -> bool:
        return self.visibility != "public"

    def matches(self, query: str) -> bool:
        """Búsqueda simple, sin distinguir mayúsculas, en nombre, descripción, lenguaje y topics.

        Todas las palabras de la consulta deben aparecer en algún campo.
        """
        haystack = " ".join(
            [self.full_name, self.description or "", self.language or "", " ".join(self.topics)]
        ).lower()
        return all(word in haystack for word in query.lower().split())

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "id": self.id,
            "full_name": self.full_name,
            "name": self.name,
            "owner": self.owner,
            "description": self.description,
            "url": self.url,
            "visibility": self.visibility,
            "archived": self.archived,
            "fork": self.fork,
            "language": self.language,
            "topics": list(self.topics),
            "default_branch": self.default_branch,
            "stars": self.stars,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class Suggestion:
    """Propuesta de la IA para un repositorio."""

    description: str
    topics: list[str] = field(default_factory=list)
    # Aviso para el usuario, p. ej. si no se pudo leer el README.
    warning: str = ""
