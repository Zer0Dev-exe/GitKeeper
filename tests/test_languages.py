import json

import respx
from conftest import FakeProvider, make_repo
from textual.widgets import Static

from gitkeeper.errors import ProviderError
from gitkeeper.providers import BitbucketProvider, GitHubProvider, GitLabProvider
from gitkeeper.providers.base import to_percentages
from gitkeeper.render import format_languages, format_topics
from gitkeeper.service import GitKeeper


def test_to_percentages_sorted_and_normalized():
    result = to_percentages({"Makefile": 25, "Python": 75, "Empty": 0})
    assert list(result) == ["Python", "Makefile"]
    assert result["Python"] == 75.0 and result["Makefile"] == 25.0
    assert to_percentages({}) == {}
    assert to_percentages({"X": 0}) == {}


def test_format_languages_and_topics():
    assert format_languages({"Python": 99.4, "Makefile": 0.6}) == "Python 99% · Makefile <1%"
    assert format_languages({}, "Go") == "Go"
    assert format_languages(None) == "No detectado"
    assert format_topics([]) == "No hay"
    assert format_topics(["cli", "ai"]) == "cli, ai"


@respx.mock
def test_github_languages():
    respx.get("https://api.github.com/repos/alice/demo/languages").respond(json={"Python": 300, "HTML": 100})
    provider = GitHubProvider("t", sleep=lambda s: None)
    assert provider.get_languages(make_repo("demo")) == {"Python": 75.0, "HTML": 25.0}


@respx.mock
def test_gitlab_languages():
    respx.get("https://gitlab.com/api/v4/projects/7/languages").respond(json={"Ruby": 60.0, "Vue": 40.0})
    provider = GitLabProvider("t", sleep=lambda s: None)
    repo = make_repo("demo", provider="gitlab", id="7")
    assert provider.get_languages(repo) == {"Ruby": 60.0, "Vue": 40.0}


def test_bitbucket_uses_main_language():
    provider = BitbucketProvider("t")
    assert provider.get_languages(make_repo(provider="bitbucket", language="Java")) == {"Java": 100.0}
    assert provider.get_languages(make_repo(provider="bitbucket")) == {}


class LangProvider(FakeProvider):
    def __init__(self, *args, languages=None, error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.languages = languages or {}
        self.error = error
        self.language_calls = 0

    def get_languages(self, repo):
        self.language_calls += 1
        if self.error:
            raise self.error
        return self.languages


def test_service_languages_cached_and_errors_tolerated():
    provider = LangProvider("github", [make_repo("app")], languages={"Python": 100.0})
    service = GitKeeper([provider])
    repo = service.resolve("app")
    assert service.cached_languages(repo) is None
    assert service.languages(repo) == {"Python": 100.0}
    service.languages(repo)
    assert provider.language_calls == 1
    assert service.cached_languages(repo) == {"Python": 100.0}

    failing = LangProvider("github", [make_repo("x")], error=ProviderError("403", 403))
    service = GitKeeper([failing])
    assert service.languages(service.resolve("x")) == {}


async def test_tui_detail_shows_archived_languages_and_topics():
    from gitkeeper.tui import GitKeeperApp

    provider = LangProvider(
        "github",
        [make_repo("app", topics=["cli"]), make_repo("bare", archived=True)],
        languages={"Python": 80.0, "HTML": 20.0},
    )
    app = GitKeeperApp(GitKeeper([provider]))
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        detail = str(app.query_one("#detail", Static).render())
        assert "Archivado: No" in detail
        assert "Lenguajes: ● Python 80% · ● HTML 20%" in detail
        assert "Topics: cli" in detail

        from gitkeeper.filters import Filters

        app.set_filters(Filters())  # sin filtros, para ver el archivado
        app.query_one("DataTable").move_cursor(row=[r.name for r in app.shown].index("bare"))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        detail = str(app.query_one("#detail", Static).render())
        assert "Archivado: Sí" in detail
        assert "Topics: No hay" in detail


def test_cli_show_includes_languages(monkeypatch):
    from rich.console import Console
    from typer.testing import CliRunner

    from gitkeeper import cli

    provider = LangProvider("github", [make_repo("app")], languages={"Go": 90.0, "Shell": 10.0})
    monkeypatch.setattr(cli, "make_service", lambda config, only=None: GitKeeper([provider]))
    monkeypatch.setattr(cli, "console", Console(width=200, color_system=None))
    result = CliRunner().invoke(cli.app, ["show", "app"])
    assert "● Go 90% · ● Shell 10%" in result.output
    assert "Topics" in result.output and "No hay" in result.output
    assert "Archivado" in result.output and "No" in result.output
    data = json.loads(CliRunner().invoke(cli.app, ["show", "app", "--json"]).output)
    assert data["languages"] == {"Go": 90.0, "Shell": 10.0}
