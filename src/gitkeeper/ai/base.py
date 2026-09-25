from __future__ import annotations

from abc import ABC, abstractmethod

from gitkeeper.ai.prompt import SYSTEM_PROMPT, build_user_prompt, parse_suggestion
from gitkeeper.models import Repo, Suggestion


class AIProvider(ABC):
    name: str = ""
    label: str = ""
    default_model: str = ""

    def __init__(self, model: str = "", api_key: str = ""):
        self.model = model or self.default_model
        self.api_key = api_key

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Devuelve el texto (JSON) generado por el modelo."""

    def suggest(
        self,
        repo: Repo,
        readme: str | None,
        files: list[str],
        language: str = "es",
        languages: dict[str, float] | None = None,
    ) -> Suggestion:
        user = build_user_prompt(repo, readme, files, language, languages)
        return parse_suggestion(self.complete(SYSTEM_PROMPT, user))

    def describe(self) -> str:
        return f"{self.label} ({self.model})"
