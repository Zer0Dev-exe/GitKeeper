import json

import httpx
import pytest
import respx

from gitkeeper.errors import AuthError, NotFoundError, ProviderError
from gitkeeper.providers.github import GitHubProvider

API = "https://api.github.com"


def gh_repo(name="demo", **extra):
    data = {
        "id": 1,
        "name": name,
        "full_name": f"alice/{name}",
        "owner": {"login": "alice"},
        "description": "Old",
        "html_url": f"https://github.com/alice/{name}",
        "private": False,
        "visibility": "public",
        "archived": False,
        "fork": False,
        "language": "Python",
        "topics": ["cli"],
        "default_branch": "main",
        "stargazers_count": 5,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2025-06-01T00:00:00Z",
    }
    data.update(extra)
    return data


@pytest.fixture
def gh():
    provider = GitHubProvider("tok", sleep=lambda s: None)
    yield provider
    provider.close()


@respx.mock
def test_auth_headers_sent(gh):
    route = respx.get(f"{API}/user").respond(json={"login": "alice"})
    assert gh.whoami() == "alice"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer tok"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["User-Agent"].startswith("gitkeeper/")


@respx.mock
def test_list_repos_follows_link_pagination(gh):
    page2 = f"{API}/user/repos?page=2"

    def handler(request):
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[gh_repo("b")])
        return httpx.Response(200, json=[gh_repo("a")], headers={"Link": f'<{page2}>; rel="next"'})

    route = respx.get(f"{API}/user/repos").mock(side_effect=handler)
    repos = gh.list_repos()
    assert [r.name for r in repos] == ["a", "b"]
    assert route.call_count == 2
    params = route.calls[0].request.url.params
    assert params["per_page"] == "100"
    assert params["affiliation"] == "owner,organization_member"


@respx.mock
def test_to_repo_mapping_uses_latest_activity(gh):
    respx.get(f"{API}/repos/alice/demo").respond(json=gh_repo(visibility="internal", private=True))
    repo = gh.get_repo("alice/demo")
    assert repo.ref == "github:alice/demo"
    assert repo.owner == "alice"
    assert repo.visibility == "internal"
    assert repo.private
    assert repo.stars == 5
    assert repo.topics == ["cli"]
    # pushed_at (junio) es más reciente que updated_at (enero)
    assert repo.updated_at.month == 6


@respx.mock
def test_missing_visibility_falls_back_to_private_flag(gh):
    data = gh_repo(private=True)
    del data["visibility"]
    respx.get(f"{API}/repos/alice/demo").respond(json=data)
    assert gh.get_repo("alice/demo").visibility == "private"


@respx.mock
def test_update_description(gh):
    route = respx.patch(f"{API}/repos/alice/demo").respond(json=gh_repo(description="New"))
    repo = gh.to_repo(gh_repo())
    updated = gh.update_description(repo, "New")
    assert json.loads(route.calls.last.request.content) == {"description": "New"}
    assert updated.description == "New"


@respx.mock
def test_set_topics_normalizes(gh):
    route = respx.put(f"{API}/repos/alice/demo/topics").respond(json={"names": []})
    repo = gh.to_repo(gh_repo())
    updated = gh.set_topics(repo, ["Machine Learning", "CLI", "cli", "c++"])
    body = json.loads(route.calls.last.request.content)
    assert body == {"names": ["machine-learning", "cli", "c"]}
    assert updated.topics == body["names"]


@respx.mock
def test_archive_and_unarchive(gh):
    route = respx.patch(f"{API}/repos/alice/demo").respond(json=gh_repo(archived=True))
    repo = gh.to_repo(gh_repo())
    assert gh.set_archived(repo, True).archived is True
    assert json.loads(route.calls.last.request.content) == {"archived": True}


@respx.mock
def test_delete(gh):
    route = respx.delete(f"{API}/repos/alice/demo").respond(204)
    gh.delete_repo(gh.to_repo(gh_repo()))
    assert route.called


@respx.mock
def test_readme_raw_and_missing(gh):
    route = respx.get(f"{API}/repos/alice/demo/readme").respond(text="# Hello")
    repo = gh.to_repo(gh_repo())
    assert gh.get_readme(repo) == "# Hello"
    assert route.calls.last.request.headers["Accept"] == "application/vnd.github.raw+json"
    route.respond(404, json={"message": "Not Found"})
    assert gh.get_readme(repo) is None


@respx.mock
def test_list_root_marks_dirs(gh):
    respx.get(f"{API}/repos/alice/demo/contents").respond(
        json=[{"name": "src", "type": "dir"}, {"name": "README.md", "type": "file"}]
    )
    assert gh.list_root(gh.to_repo(gh_repo())) == ["src/", "README.md"]


@respx.mock
def test_error_mapping(gh):
    route = respx.get(f"{API}/repos/alice/x")
    route.respond(401, json={"message": "Bad credentials"})
    with pytest.raises(AuthError, match="Bad credentials"):
        gh.get_repo("alice/x")
    route.respond(404, json={"message": "Not Found"})
    with pytest.raises(NotFoundError):
        gh.get_repo("alice/x")
    route.respond(403, json={"message": "Resource not accessible"})
    with pytest.raises(ProviderError, match="403"):
        gh.get_repo("alice/x")
    route.respond(422, json={"message": "Validation Failed"})
    with pytest.raises(ProviderError, match="Validation Failed") as info:
        gh.get_repo("alice/x")
    assert info.value.status_code == 422


@respx.mock
def test_fine_grained_token_missing_permission_hints(gh):
    denied = {"message": "Resource not accessible by personal access token"}
    repo = gh.to_repo(gh_repo())
    respx.get(f"{API}/repos/alice/demo/readme").respond(403, json=denied)
    with pytest.raises(ProviderError, match="Contents: Read-only"):
        gh.get_readme(repo)
    respx.patch(f"{API}/repos/alice/demo").respond(403, json=denied)
    with pytest.raises(ProviderError, match="Administration: Read and write"):
        gh.update_description(repo, "x")
    respx.delete(f"{API}/repos/alice/demo").respond(403, json=denied)
    with pytest.raises(ProviderError, match="Administration"):
        gh.delete_repo(repo)


@respx.mock
def test_retries_on_rate_limit(gh):
    route = respx.get(f"{API}/user").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"login": "a"})]
    )
    assert gh.whoami() == "a"
    assert route.call_count == 2


@respx.mock
def test_network_error_after_retries(gh):
    respx.get(f"{API}/user").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ProviderError, match="red"):
        gh.whoami()


@respx.mock
def test_enterprise_api_url():
    provider = GitHubProvider("tok", api_url="https://ghe.corp/api/v3/", sleep=lambda s: None)
    route = respx.get("https://ghe.corp/api/v3/user").respond(json={"login": "bob"})
    assert provider.whoami() == "bob"
    assert route.called
