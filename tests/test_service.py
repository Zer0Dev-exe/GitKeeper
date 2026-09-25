from datetime import timedelta

import pytest
from conftest import NOW, FakeAI, FakeProvider, make_repo

from gitkeeper.errors import AuthError, GitKeeperError, ProviderError, RepoResolutionError, UnsupportedOperation
from gitkeeper.service import GitKeeper, filter_repos, sort_repos


def repos():
    return [
        make_repo("alpha", language="Python", description="Web scraper", stars=5,
                  created_at=NOW - timedelta(days=100), updated_at=NOW - timedelta(days=2)),
        make_repo("beta", language="Go", visibility="private", archived=True, stars=1,
                  created_at=NOW - timedelta(days=10), updated_at=NOW - timedelta(days=50)),
        make_repo("gamma", fork=True, stars=9,
                  created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=1)),
    ]


def service_with(*providers, ai=None):
    return GitKeeper(list(providers), ai_factory=(lambda: ai) if ai else None)


# -------------------------------------------------------------- filtros/orden
def test_filter_repos():
    data = repos()
    assert [r.name for r in filter_repos(data, archived=False)] == ["alpha", "gamma"]
    assert [r.name for r in filter_repos(data, archived=True)] == ["beta"]
    assert [r.name for r in filter_repos(data, visibility="private")] == ["beta"]
    assert [r.name for r in filter_repos(data, language="python")] == ["alpha"]
    assert [r.name for r in filter_repos(data, forks=False)] == ["alpha", "beta"]
    assert [r.name for r in filter_repos(data, query="scraper")] == ["alpha"]
    assert filter_repos(data, provider="gitlab") == []


def test_sort_repos():
    data = repos()
    assert [r.name for r in sort_repos(data, "updated")] == ["gamma", "alpha", "beta"]
    assert [r.name for r in sort_repos(data, "created")] == ["gamma", "beta", "alpha"]
    assert [r.name for r in sort_repos(data, "name")] == ["alpha", "beta", "gamma"]
    assert [r.name for r in sort_repos(data, "stars")] == ["gamma", "alpha", "beta"]
    assert [r.name for r in sort_repos(data, "name", reverse=True)] == ["gamma", "beta", "alpha"]
    with pytest.raises(GitKeeperError):
        sort_repos(data, "size")


def test_sort_handles_missing_dates():
    data = [make_repo("a", updated_at=None), make_repo("b")]
    assert [r.name for r in sort_repos(data, "updated")] == ["b", "a"]


# ------------------------------------------------------------------ listados
def test_requires_providers():
    with pytest.raises(AuthError, match="auth login"):
        GitKeeper([]).fetch_all()


def test_fetch_all_merges_and_reports_errors():
    gh = FakeProvider("github", repos())
    gl = FakeProvider("gitlab", fail=ProviderError("GitLab: caído"))
    bb = FakeProvider("bitbucket", [make_repo("delta", provider="bitbucket")])
    result = service_with(gh, gl, bb).fetch_all()
    assert sorted(r.name for r in result.repos) == ["alpha", "beta", "delta", "gamma"]
    assert result.errors == {"gitlab": "GitLab: caído"}


def test_fetch_all_cache_and_refresh():
    gh = FakeProvider("github", repos())
    service = service_with(gh)
    service.fetch_all()
    service.fetch_all()
    assert gh.calls.count(("list",)) == 1
    service.fetch_all(refresh=True)
    assert gh.calls.count(("list",)) == 2
    service.fetch_all(only=["github"])
    assert gh.calls.count(("list",)) == 3


def test_recent_latest_search():
    service = service_with(FakeProvider("github", repos()))
    assert [r.name for r in service.recent(10).repos] == ["gamma", "alpha"]
    assert [r.name for r in service.recent(10, include_archived=True).repos] == ["gamma", "alpha", "beta"]
    assert [r.name for r in service.recent(1).repos] == ["gamma"]
    assert [r.name for r in service.latest(10).repos] == ["gamma", "alpha"]
    assert [r.name for r in service.search("go").repos] == ["beta"]
    assert service.search("go", include_archived=False).repos == []


# ---------------------------------------------------------------- resolución
@pytest.fixture
def multi():
    gh = FakeProvider("github", [make_repo("app"), make_repo("shared")])
    gl = FakeProvider("gitlab", [make_repo("shared", provider="gitlab", owner="team/sub"),
                                 make_repo("lib", provider="gitlab", owner="team")])
    return service_with(gh, gl)


def test_parse_ref(multi):
    assert multi.parse_ref("github:alice/app") == ("github", "alice/app")
    assert multi.parse_ref("gl:team/lib") == ("gitlab", "team/lib")
    assert multi.parse_ref("alice/app/") == (None, "alice/app")
    assert multi.parse_ref("https://github.com/alice/app.git") == ("github", "alice/app")
    assert multi.parse_ref("https://github.com/alice/app/tree/main/src") == ("github", "alice/app")
    assert multi.parse_ref("https://gitlab.com/team/sub/shared/-/tree/main") == ("gitlab", "team/sub/shared")
    assert multi.parse_ref("https://bitbucket.org/acme/demo/src/main/") == ("bitbucket", "acme/demo")
    # Host de un proveedor self-hosted (api.<name>.example en FakeProvider)
    assert multi.parse_ref("https://github.example/alice/app") == ("github", "alice/app")


def test_resolve_by_bare_name(multi):
    assert multi.resolve("app").ref == "github:alice/app"
    assert multi.resolve("LIB").ref == "gitlab:team/lib"


def test_resolve_ambiguous_and_missing(multi):
    with pytest.raises(RepoResolutionError, match="ambiguo"):
        multi.resolve("shared")
    with pytest.raises(RepoResolutionError, match="Quizá: app"):
        multi.resolve("apq")
    with pytest.raises(RepoResolutionError):
        multi.resolve("alice/nothing")
    with pytest.raises(RepoResolutionError):
        multi.resolve("  ")


def test_resolve_full_name_and_prefix(multi):
    assert multi.resolve("team/sub/shared").provider == "gitlab"
    assert multi.resolve("github:alice/shared").provider == "github"
    assert multi.resolve("gitlab:shared").ref == "gitlab:team/sub/shared"
    with pytest.raises(AuthError):
        multi.resolve("bitbucket:acme/demo")


def test_resolve_full_name_uses_cache():
    gh = FakeProvider("github", [make_repo("app")])
    service = service_with(gh)
    service.fetch_all()
    assert service.resolve("alice/app").name == "app"
    assert not any(call[0] == "get" for call in gh.calls)


# --------------------------------------------------------------- operaciones
def test_suggest_passes_context():
    ai = FakeAI()
    gh = FakeProvider("github", [make_repo("app")], readme="# App readme", files=["app.py"])
    service = service_with(gh, ai=ai)
    service.language = "en"
    suggestion = service.suggest(service.resolve("app"))
    assert suggestion.description == "Generated description"
    user_prompt = ai.prompts[0][1]
    assert "# App readme" in user_prompt and "app.py" in user_prompt and "English" in user_prompt


def test_suggest_without_ai():
    service = service_with(FakeProvider("github", [make_repo("app")]))
    with pytest.raises(GitKeeperError, match="IA"):
        service.suggest(service.resolve("app"))


def test_update_description_with_topics_and_cache():
    gh = FakeProvider("github", [make_repo("app")])
    service = service_with(gh)
    service.fetch_all()
    repo = service.resolve("app")
    updated = service.update_description(repo, "  Nueva  ", ["cli"])
    assert updated.description == "Nueva" and updated.topics == ["cli"]
    assert ("topics", "alice/app", ["cli"]) in gh.calls
    assert service.fetch_all().repos[0].description == "Nueva"


def test_update_description_ignores_topics_when_unsupported():
    bb = FakeProvider("bitbucket", [make_repo("app", provider="bitbucket")], supports_topics=False)
    service = service_with(bb)
    service.update_description(service.resolve("app"), "D", ["cli"])
    assert not any(call[0] == "topics" for call in bb.calls)


def test_archive_and_delete_update_cache():
    gh = FakeProvider("github", [make_repo("app"), make_repo("other")])
    service = service_with(gh)
    service.fetch_all()
    app = service.resolve("app")
    assert service.set_archived(app, True).archived
    assert [r.archived for r in service.fetch_all().repos if r.name == "app"] == [True]
    service.delete(app)
    assert [r.name for r in service.fetch_all().repos] == ["other"]


def test_archive_unsupported():
    bb = FakeProvider("bitbucket", [make_repo("app", provider="bitbucket")], supports_archive=False)
    service = service_with(bb)
    with pytest.raises(UnsupportedOperation):
        service.set_archived(service.resolve("app"), True)


def test_close_closes_providers():
    gh = FakeProvider("github")
    service_with(gh).close()
    assert gh.closed


class NoContentProvider(FakeProvider):
    """Token sin permiso de lectura de contenido (403 en README y ficheros)."""

    def get_readme(self, repo):
        raise ProviderError("GitHub: tu token no tiene el permiso 'Contents: Read-only'", 403)

    def list_root(self, repo):
        raise ProviderError("403", 403)

    def get_languages(self, repo):
        return {"Python": 70.0, "HTML": 30.0}


def test_suggest_without_content_permission_uses_metadata():
    ai = FakeAI()
    service = service_with(NoContentProvider("github", [make_repo("app")]), ai=ai)
    suggestion = service.suggest(service.resolve("app"))
    assert suggestion.description == "Generated description"
    assert "Sin permiso" in suggestion.warning
    prompt = ai.prompts[0][1]
    assert "Languages: Python 70%, HTML 30%" in prompt
    assert "no README" in prompt


def test_suggest_other_errors_still_fail():
    class Broken(FakeProvider):
        def get_readme(self, repo):
            raise ProviderError("GitHub: error 500", 500)

    service = service_with(Broken("github", [make_repo("app")]), ai=FakeAI())
    with pytest.raises(ProviderError, match="500"):
        service.suggest(service.resolve("app"))


def test_suggest_has_no_warning_with_access():
    service = service_with(FakeProvider("github", [make_repo("app")]), ai=FakeAI())
    assert service.suggest(service.resolve("app")).warning == ""
