"""Construcción del prompt y parseo de la respuesta de la IA (común a todos los proveedores)."""

from __future__ import annotations

import json
import re
from typing import Any

from gitkeeper.errors import AIError
from gitkeeper.models import Repo, Suggestion
from gitkeeper.providers.base import normalize_topics

# Límite de GitHub (el más restrictivo de las tres plataformas).
MAX_DESCRIPTION = 350
# Para describir un repo basta el principio del README; evita prompts enormes.
README_LIMIT = 12_000
MAX_FILES = 80

LANGUAGE_NAMES = {
    "es": "Spanish",
    "en": "English",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ca": "Catalan",
    "eu": "Basque",
    "gl": "Galician",
}

SYSTEM_PROMPT = """You write repository descriptions for GitHub, GitLab and Bitbucket.

A good description is one sentence (two at most) that tells a stranger what the project is \
and what it does, so they can decide in a few seconds whether it is relevant to them. \
Lead with what it is, mention the main technology only when it helps, and avoid filler \
such as "This repository contains" or "A simple project". No emojis, no Markdown, no \
trailing period needed. Keep it under 160 characters when possible and never over 350.

Also suggest up to 6 topics: lowercase keywords with hyphens instead of spaces \
(e.g. "cli", "machine-learning", "fastapi") describing the domain and main technologies.

Base everything on the evidence provided (README, file names, metadata). If there is \
very little evidence, write a cautious description from the name and files rather than \
inventing features.

Reply only with a JSON object: {"description": "...", "topics": ["..."]}."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["description", "topics"],
    "additionalProperties": False,
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.lower(), code)


def build_user_prompt(
    repo: Repo,
    readme: str | None,
    files: list[str],
    language: str = "es",
    languages: dict[str, float] | None = None,
) -> str:
    parts = [
        f"Write the description in {language_name(language)}.",
        "",
        f"Repository: {repo.full_name} ({repo.provider})",
        f"Current description: {repo.description or '(none)'}",
    ]
    if languages:
        breakdown = ", ".join(f"{name} {round(share)}%" for name, share in list(languages.items())[:8])
        parts.append(f"Languages: {breakdown}")
    elif repo.language:
        parts.append(f"Main language: {repo.language}")
    if repo.topics:
        parts.append(f"Current topics: {', '.join(repo.topics)}")
    if repo.fork:
        parts.append("This repository is a fork.")
    if files:
        shown = files[:MAX_FILES]
        extra = f" (+{len(files) - len(shown)} more)" if len(files) > len(shown) else ""
        parts.append(f"Top-level files: {', '.join(shown)}{extra}")
    if readme and readme.strip():
        text = readme.strip()
        note = ""
        if len(text) > README_LIMIT:
            text = text[:README_LIMIT]
            note = " (first part only; the README continues)"
        parts += ["", f"README{note}:", "<readme>", text, "</readme>"]
    else:
        parts += ["", "The repository has no README."]
    return "\n".join(parts)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _extract_json(text: str) -> Any:
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise AIError("La IA no devolvió un JSON válido.")


def clean_description(text: str) -> str:
    description = " ".join(text.split()).strip().strip('"').strip()
    if len(description) > MAX_DESCRIPTION:
        cut = description[: MAX_DESCRIPTION - 1]
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
        description = cut.rstrip(",;:- ") + "…"
    return description


def parse_suggestion(text: str) -> Suggestion:
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise AIError("La IA devolvió un formato inesperado.")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise AIError("La IA no devolvió ninguna descripción.")
    topics = data.get("topics") or []
    if not isinstance(topics, list):
        topics = []
    return Suggestion(
        description=clean_description(description),
        topics=normalize_topics([t for t in topics if isinstance(t, str)])[:6],
    )
