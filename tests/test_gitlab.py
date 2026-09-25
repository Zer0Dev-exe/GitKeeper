import json

import httpx
import pytest
import respx

from gitkeeper.providers.gitlab import GitLabProvider, encode_path

API = "https://gitlab.com/api/v4"


def gl_project(pid=10, path="group/sub/demo", **extra):
    data = {
        "id": pid,
        "name": "Demo",
        "path": path.split("/")[-1],
        "path_with_namespace": path,
        "namespace": {"full_path": path.rsplit("/", 1)[0]},
        "description": None,
        "web_url": f"https://gitlab.com/{path}",
        "visibility": "internal",
        "archived": False,
        "topics": ["api"],
        "default_branch": "master",
        "star_count": 2,
        "created_at": "2024-01-01T10:00:00.000Z",
        "last_activity_at": "2025-03-01T10:00:00.000Z",
    }
    data.update(extra)
    return data


@pytest.fixture
def gl():
    provider = GitLabProvider("glpat-x", sleep=lambda s: None)
    yield provider
    provider.close()


def test_encode_path():
    assert encode_path("group/sub/demo") == "group%2Fsub%2Fdemo"
    assert encode_path("docs/READ ME.md") == "docs%2FREAD%20ME.md"


@respx.mock
def test_whoami_uses_bearer(gl):
    route = respx.get(f"{API}/user").respond(json={"username": "alice"})
    assert gl.whoami() == "alice"
    assert route.calls.last.request.headers["Authorization"] == "Bearer glpat-x"


@respx.mock
def test_list_repos_link_pagination_and_params(gl):
    nxt = f"{API}/projects?page=2&per_page=100"
    first = respx.get(f"{API}/projects", params={"membership": "true"}).mock(
        side_effect=[httpx.Response(200, json=[gl_project(1, "a/one")], headers={"Link": f'<{nxt}>; rel="next"'})]
    )
    respx.get(f"{API}/projects", params={"page": "2"}).respond(json=[gl_project(2, "a/two")])
    repos = gl.list_repos()
    assert [r.full_name for r in repos] == ["a/one", "a/two"]
    assert first.calls.last.request.url.params["order_by"] == "last_activity_at"


@respx.mock
def test_list_repos_x_next_page_fallback(gl):
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[gl_project(2, "a/two")], headers={"X-Next-Page": ""})
        return httpx.Response(200, json=[gl_project(1, "a/one")], headers={"X-Next-Page": "2"})

    respx.get(f"{API}/projects").mock(side_effect=handler)
    repos = gl.list_repos()
    assert [r.full_name for r in repos] == ["a/one", "a/two"]
    # La segunda página conserva los filtros originales.
    assert calls[1]["membership"] == "true" and calls[1]["page"] == "2"


@respx.mock
def test_owned_scope():
    provider = GitLabProvider("t", scope="owned", sleep=lambda s: None)
    route = respx.get(f"{API}/projects").respond(json=[])
    provider.list_repos()
    params = route.calls.last.request.url.params
    assert params["owned"] == "true" and "membership" not in params


@respx.mock
def test_mapping(gl):
    respx.get(f"{API}/projects/group%2Fsub%2Fdemo").respond(json=gl_project(forked_from_project={"id": 1}))
    repo = gl.get_repo("group/sub/demo")
    assert repo.id == "10"
    assert repo.owner == "group/sub"
    assert repo.name == "demo"
    assert repo.description == ""
    assert repo.visibility == "internal" and repo.private
    assert repo.fork is True
    assert repo.topics == ["api"]
    assert repo.updated_at.month == 3


@respx.mock
def test_get_repo_encodes_path_in_url(gl):
    route = respx.get(url__regex=r".*/projects/.*").respond(json=gl_project())
    gl.get_repo("group/sub/demo")
    assert "/projects/group%2Fsub%2Fdemo" in str(route.calls.last.request.url)


@respx.mock
def test_update_description_and_topics(gl):
    route = respx.put(f"{API}/projects/10").mock(
        side_effect=[
            httpx.Response(200, json=gl_project(description="New")),
            httpx.Response(200, json=gl_project(description="New", topics=["a-b"])),
        ]
    )
    repo = gl.to_repo(gl_project())
    assert gl.update_description(repo, "New").description == "New"
    assert json.loads(route.calls[0].request.content) == {"description": "New"}
    updated = gl.set_topics(repo, ["A B"])
    assert json.loads(route.calls[1].request.content) == {"topics": ["a-b"]}
    assert updated.topics == ["a-b"]


@respx.mock
def test_archive_unarchive_delete(gl):
    repo = gl.to_repo(gl_project())
    arch = respx.post(f"{API}/projects/10/archive").respond(json=gl_project(archived=True))
    unarch = respx.post(f"{API}/projects/10/unarchive").respond(json=gl_project(archived=False))
    delete = respx.delete(f"{API}/projects/10").respond(202, json={"message": "202 Accepted"})
    assert gl.set_archived(repo, True).archived is True
    assert gl.set_archived(repo, False).archived is False
    gl.delete_repo(repo)
    assert arch.called and unarch.called and delete.called


@respx.mock
def test_readme_via_tree(gl):
    repo = gl.to_repo(gl_project())
    tree = respx.get(f"{API}/projects/10/repository/tree").respond(
        json=[{"name": "docs", "type": "tree"}, {"name": "README.rst", "type": "blob"},
              {"name": "readme.md", "type": "blob"}]
    )
    raw = respx.get(f"{API}/projects/10/repository/files/readme.md/raw").respond(text="# Hi")
    assert gl.get_readme(repo) == "# Hi"
    assert tree.calls.last.request.url.params["ref"] == "master"
    assert raw.calls.last.request.url.params["ref"] == "master"


@respx.mock
def test_readme_empty_repo(gl):
    repo = gl.to_repo(gl_project())
    respx.get(f"{API}/projects/10/repository/tree").respond(404, json={"message": "404 Tree Not Found"})
    assert gl.get_readme(repo) is None


@respx.mock
def test_error_message_list_format(gl):
    respx.put(f"{API}/projects/10").respond(400, json={"message": {"description": ["is too long"]}})
    with pytest.raises(Exception, match="400"):
        gl.update_description(gl.to_repo(gl_project()), "x" * 5000)
