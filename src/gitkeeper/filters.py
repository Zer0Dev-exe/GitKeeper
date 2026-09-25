"""Filtros combinables de la TUI.

Dentro de un grupo las opciones se combinan con O (``Públicos`` o ``Privados``) y entre
grupos con Y (``Privados`` y ``Sin descripción``). Un grupo sin nada marcado no filtra.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from gitkeeper.models import Repo

NO_LANGUAGE = "__none__"

LanguageOf = Callable[[Repo], "str | None"]


@dataclass(frozen=True)
class FilterGroup:
    key: str
    title: str
    options: tuple[tuple[str, str], ...]  # (valor, etiqueta)


STATIC_GROUPS: tuple[FilterGroup, ...] = (
    FilterGroup("status", "Estado", (("active", "Activos"), ("archived", "Archivados"))),
    FilterGroup("visibility", "Visibilidad", (("public", "Públicos"), ("private", "Privados"), ("internal", "Internos"))),
    FilterGroup("kind", "Tipo", (("source", "Originales"), ("fork", "Forks"))),
    FilterGroup("description", "Descripción", (("with", "Con descripción"), ("without", "Sin descripción"))),
    FilterGroup("topics", "Topics", (("with", "Con topics"), ("without", "Sin topics"))),
    FilterGroup("activity", "Última actividad", (
        ("week", "Última semana"),
        ("month", "Último mes"),
        ("year", "Último año"),
        ("older", "Hace más de un año"),
    )),
)

PROVIDER_LABELS = {"github": "GitHub", "gitlab": "GitLab", "bitbucket": "Bitbucket"}
ACTIVITY_DAYS = {"week": 7, "month": 30, "year": 365}


def default_language_of(repo: Repo) -> str | None:
    return repo.language


def dynamic_groups(repos: Iterable[Repo], language_of: LanguageOf = default_language_of) -> list[FilterGroup]:
    """Grupos que dependen de los repos cargados: plataforma, lenguaje y propietario."""
    repos = list(repos)
    groups: list[FilterGroup] = []
    providers = sorted({r.provider for r in repos})
    if len(providers) > 1:
        groups.append(FilterGroup("provider", "Plataforma",
                                  tuple((p, PROVIDER_LABELS.get(p, p)) for p in providers)))
    counts: dict[str, int] = {}
    for repo in repos:
        lang = language_of(repo) or NO_LANGUAGE
        counts[lang] = counts.get(lang, 0) + 1
    ordered = sorted((k for k in counts if k != NO_LANGUAGE), key=lambda k: (-counts[k], k.lower()))
    if NO_LANGUAGE in counts:
        ordered.append(NO_LANGUAGE)
    groups.append(FilterGroup("language", "Lenguaje", tuple(
        (k, f"{'Sin código' if k == NO_LANGUAGE else k} ({counts[k]})") for k in ordered
    )))
    owners: dict[str, int] = {}
    for repo in repos:
        owners[repo.owner] = owners.get(repo.owner, 0) + 1
    if len(owners) > 1:
        groups.append(FilterGroup("owner", "Propietario", tuple(
            (o, f"{o} ({owners[o]})") for o in sorted(owners, key=lambda o: (-owners[o], o.lower()))
        )))
    return groups


def all_groups(repos: Iterable[Repo], language_of: LanguageOf = default_language_of) -> list[FilterGroup]:
    repos = list(repos)
    groups = list(STATIC_GROUPS)
    if not any(r.visibility == "internal" for r in repos):
        # "Internos" solo existe en GitLab: no se ofrece si no hay ninguno.
        visibility = groups[1]
        groups[1] = FilterGroup(visibility.key, visibility.title,
                                tuple(o for o in visibility.options if o[0] != "internal"))
    return groups + dynamic_groups(repos, language_of)


@dataclass
class Filters:
    selected: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "Filters":
        return cls({"status": {"active"}})

    def copy(self) -> "Filters":
        return Filters({k: set(v) for k, v in self.selected.items() if v})

    def count(self) -> int:
        return sum(len(v) for v in self.selected.values())

    def is_empty(self) -> bool:
        return self.count() == 0

    def matches(self, repo: Repo, language_of: LanguageOf = default_language_of,
                now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        for group, values in self.selected.items():
            if values and not any(self._match(group, value, repo, language_of, now) for value in values):
                return False
        return True

    @staticmethod
    def _match(group: str, value: str, repo: Repo, language_of: LanguageOf, now: datetime) -> bool:
        if group == "status":
            return repo.archived == (value == "archived")
        if group == "visibility":
            return repo.visibility == value
        if group == "kind":
            return repo.fork == (value == "fork")
        if group == "description":
            return bool(repo.description.strip()) == (value == "with")
        if group == "topics":
            return bool(repo.topics) == (value == "with")
        if group == "activity":
            if repo.updated_at is None:
                return value == "older"
            age = now - repo.updated_at
            if value == "older":
                return age > timedelta(days=365)
            return age <= timedelta(days=ACTIVITY_DAYS[value])
        if group == "provider":
            return repo.provider == value
        if group == "language":
            return (language_of(repo) or NO_LANGUAGE) == value
        if group == "owner":
            return repo.owner == value
        return True

    def apply(self, repos: Iterable[Repo], language_of: LanguageOf = default_language_of,
              now: datetime | None = None) -> list[Repo]:
        now = now or datetime.now(timezone.utc)
        return [r for r in repos if self.matches(r, language_of, now)]

    def summary(self, groups: Iterable[FilterGroup] | None = None) -> str:
        """Texto corto con las opciones activas, p. ej. 'Activos, Sin descripción'."""
        if self.is_empty():
            return "sin filtros"
        labels: dict[tuple[str, str], str] = {}
        for group in groups or STATIC_GROUPS:
            for value, label in group.options:
                labels[(group.key, value)] = label.rsplit(" (", 1)[0]
        parts = []
        for group, values in self.selected.items():
            for value in sorted(values):
                parts.append(labels.get((group, value), "Sin código" if value == NO_LANGUAGE else value))
        return ", ".join(parts)
