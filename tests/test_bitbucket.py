import base64
import json

import pytest
import respx

from gitkeeper.errors import GitKeeperError, UnsupportedOperation
from gitkeeper.providers.bitbucket import BitbucketProvider

API = "https://api.bitbucket.org/2.0"


def bb_repo(slug="demo", ws="acme", **extra):
    data = {
        "uuid": "{1234}",
        "slug": slug,
        "name": slug.capitalize(),
        "full_name": f"{ws}/{slug}",
        "workspace": {"slug": ws},
        "description": "",
        "is_private": True,
        "language": "",
        "links": {"html": {"href": f"https://bitbucket.org/{ws}/{slug}"}},
        "mainbranch": {"name": "main"},
        "created_on": "2023-05-01T08:00:00.000000+00:00",
        "updated_on": "2025-02-01T08:00:00.000000+00:00",
    }
    data.update(extra)
    return data


def make(**kwargs):
    return BitbucketProvider("tok", sleep=lambda s: None, **kwargs)


def test_basic_auth_with_username():
    provider = make(username="me@example.com")
    expected = base64.b64encode(b"me@example.com:tok").decode()
    assert provider.client.headers["Authorization"] == f"Basic {expected}"


def test_bearer_without_username():
    assert make().client.headers["Authorization"] == "Bearer tok"


def test_capabilities():
    provider = make()
    assert not provider.supports_topics and not provider.supports_archive
    repo = provider.to_repo(bb_repo())
    with pytest.raises(UnsupportedOperation):
        provider.set_archived(repo, True)
    with pytest.raises(UnsupportedOperation):
        provider.set_topics(repo, ["x"])


def test_mapping():
    repo = make().to_repo(bb_repo(parent={"full_name": "x/y"}))
    assert repo.ref == "bitbucket:acme/demo"
    assert repo.id == "{1234}"
    assert repo.owner == "acme"
    assert repo.visibility == "private"
    assert repo.language is None
    assert repo.fork is True
    assert repo.default_branch == "main"
    assert repo.url == "https://bitbucket.org/acme/demo"


@respx.mock
def test_list_all_member_repos_with_json_pagination():
    nxt = f"{API}/repositories?role=member&page=2"
    respx.get(f"{API}/repositories", params={"page": "2"}).respond(json={"values": [bb_repo("two")]})
    first = respx.get(f"{API}/repositories").respond(json={"values": [bb_repo("one")], "next": nxt})
    repos = make().list_repos()
    assert [r.name for r in repos] == ["one", "two"]
    assert first.calls[0].request.url.params["role"] == "member"


@respx.mock
def test_list_by_workspaces():
    a = respx.get(f"{API}/repositories/ws1").respond(json={"values": [bb_repo("a", "ws1")]})
    b = respx.get(f"{API}/repositories/ws2").respond(json={"values": [bb_repo("b", "ws2")]})
    repos = make(workspace="ws1, ws2").list_repos()
    assert [r.full_name for r in repos] == ["ws1/a", "ws2/b"]
    assert a.called and b.called


@respx.mock
def test_list_error_suggests_workspace():
    respx.get(f"{API}/repositories").respond(400, json={"error": {"message": "role requires workspace"}})
    with pytest.raises(GitKeeperError, match="bitbucket.workspace"):
        make().list_repos()


@respx.mock
def test_whoami_user_and_workspace_token_fallback():
    route = respx.get(f"{API}/user").respond(json={"username": "me", "display_name": "Me"})
    assert make().whoami() == "me"
    route.respond(403, json={"error": {"message": "forbidden"}})
    respx.get(f"{API}/repositories/acme").respond(json={"values": []})
    assert make(workspace="acme").whoami() == "workspace:acme"


@respx.mock
def test_update_and_delete():
    provider = make()
    repo = provider.to_repo(bb_repo())
    put = respx.put(f"{API}/repositories/acme/demo").respond(json=bb_repo(description="New"))
    delete = respx.delete(f"{API}/repositories/acme/demo").respond(204)
    assert provider.update_description(repo, "New").description == "New"
    assert json.loads(put.calls.last.request.content) == {"description": "New"}
    provider.delete_repo(repo)
    assert delete.called


@respx.mock
def test_readme_via_src():
    provider = make()
    repo = provider.to_repo(bb_repo())
    respx.get(f"{API}/repositories/acme/demo/src/main/").respond(
        json={"values": [{"path": "src", "type": "commit_directory"}, {"path": "README.md", "type": "commit_file"}]}
    )
    respx.get(f"{API}/repositories/acme/demo/src/main/README.md").respond(text="# BB")
    assert provider.list_root(repo) == ["src/", "README.md"]
    assert provider.get_readme(repo) == "# BB"


@respx.mock
def test_error_message_nested():
    respx.get(f"{API}/repositories/acme/nope").respond(404, json={"type": "error", "error": {"message": "Repository not found"}})
    with pytest.raises(GitKeeperError, match="Repository not found"):
        make().get_repo("acme/nope")
