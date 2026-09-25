from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gitkeeper.ai.base import AIProvider
from gitkeeper.auth import TOKEN_ENV_VARS, USERNAME_ENV_VARS
from gitkeeper.config import Config
from gitkeeper.errors import NotFoundError
from gitkeeper.models import Repo
from gitkeeper.providers.base import Provider

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

AI_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Ningún test debe leer tokens reales ni la configuración real del usuario."""
    for names in list(TOKEN_ENV_VARS.values()) + list(USERNAME_ENV_VARS.values()):
        for name in names:
            monkeypatch.delenv(name, raising=False)
    for name in AI_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITKEEPER_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr("gitkeeper.auth._gh_cli_token", lambda: None)
    # Que la detección automática no encuentre las CLIs (claude, codex, gemini) instaladas en la máquina.
    monkeypatch.setattr("gitkeeper.ai.cli_agents.CLIProvider.is_available", lambda self: False)


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(path=tmp_path / "config.toml")


def make_repo(name: str = "demo", provider: str = "github", owner: str = "alice", **kwargs) -> Repo:
    defaults = dict(
        provider=provider,
        id=kwargs.pop("id", f"{provider}-{owner}-{name}"),
        full_name=f"{owner}/{name}",
        name=name,
        owner=owner,
        description="",
        url=f"https://{provider}.example/{owner}/{name}",
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=1),
        default_branch="main",
    )
    defaults.update(kwargs)
    return Repo(**defaults)


class FakeProvider(Provider):
    """Proveedor en memoria que registra las llamadas."""

    def __init__(self, name: str = "github", repos: list[Repo] | None = None,
                 supports_topics: bool = True, supports_archive: bool = True,
                 readme: str | None = "# Demo\nA demo project.", files: list[str] | None = None,
                 fail: Exception | None = None):
        self.name = name
        self.label = name.capitalize()
        self.supports_topics = supports_topics
        self.supports_archive = supports_archive
        self.repos = {r.full_name: r for r in (repos or [])}
        self.readme = readme
        self.files = files if files is not None else ["README.md", "src/"]
        self.fail = fail
        self.calls: list[tuple] = []
        self.api_url = f"https://api.{name}.example"
        self.closed = False

    def auth_headers(self) -> dict[str, str]:  # pragma: no cover
        return {}

    def whoami(self) -> str:
        return "alice"

    def list_repos(self) -> list[Repo]:
        self.calls.append(("list",))
        if self.fail:
            raise self.fail
        return [copy.copy(r) for r in self.repos.values()]

    def get_repo(self, full_name: str) -> Repo:
        self.calls.append(("get", full_name))
        for key, repo in self.repos.items():
            if key.lower() == full_name.lower():
                return copy.copy(repo)
        raise NotFoundError("no encontrado", 404)

    def update_description(self, repo: Repo, description: str) -> Repo:
        self.calls.append(("description", repo.full_name, description))
        stored = self.repos[repo.full_name]
        stored.description = description
        return copy.copy(stored)

    def set_topics(self, repo: Repo, topics: list[str]) -> Repo:
        if not self.supports_topics:
            return super().set_topics(repo, topics)
        self.calls.append(("topics", repo.full_name, list(topics)))
        stored = self.repos[repo.full_name]
        stored.topics = list(topics)
        return copy.copy(stored)

    def set_archived(self, repo: Repo, archived: bool) -> Repo:
        if not self.supports_archive:
            return super().set_archived(repo, archived)
        self.calls.append(("archive", repo.full_name, archived))
        stored = self.repos[repo.full_name]
        stored.archived = archived
        return copy.copy(stored)

    def delete_repo(self, repo: Repo) -> None:
        self.calls.append(("delete", repo.full_name))
        del self.repos[repo.full_name]

    def list_root(self, repo: Repo) -> list[str]:
        return list(self.files)

    def read_file(self, repo: Repo, path: str) -> str | None:
        return self.readme

    def get_readme(self, repo: Repo) -> str | None:
        return self.readme

    def close(self) -> None:
        self.closed = True


class FakeAI(AIProvider):
    name = "fake"
    label = "Fake"
    default_model = "fake-1"

    def __init__(self, responses: list[str] | None = None):
        super().__init__()
        self.responses = responses or ['{"description": "Generated description", "topics": ["cli", "python"]}']
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        index = min(len(self.prompts) - 1, len(self.responses) - 1)
        return self.responses[index]


@pytest.fixture
def fake_ai() -> FakeAI:
    return FakeAI()


def write_config(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
