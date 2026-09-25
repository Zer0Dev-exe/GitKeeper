"""Proveedor Bitbucket Cloud (REST API 2.0).

Autenticación:
- API token de Atlassian (o app password antiguo) + email/usuario -> HTTP Basic.
- Access token de workspace/proyecto/repositorio (sin usuario) -> Bearer.

Limitaciones de Bitbucket Cloud: no tiene topics ni permite archivar por API.
"""

from __future__ import annotations

import base64
from typing import Any, Iterator

from gitkeeper.errors import AuthError, GitKeeperError, NotFoundError, ProviderError
from gitkeeper.models import Repo, parse_datetime
from gitkeeper.providers.base import Provider


class BitbucketProvider(Provider):
    name = "bitbucket"
    label = "Bitbucket"
    supports_topics = False
    supports_archive = False

    def __init__(self, token: str, api_url: str = "https://api.bitbucket.org/2.0",
                 username: str = "", workspace: str = "", **kwargs: Any):
        self.username = username or ""
        self.workspaces = [w.strip() for w in (workspace or "").split(",") if w.strip()]
        super().__init__(token, api_url, **kwargs)

    def auth_headers(self) -> dict[str, str]:
        if self.username:
            raw = f"{self.username}:{self.token}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
        return {"Authorization": f"Bearer {self.token}"}

    def to_repo(self, data: dict[str, Any]) -> Repo:
        workspace = (data.get("workspace") or {}).get("slug")
        slug = data.get("slug")
        full_name = f"{workspace}/{slug}" if workspace and slug else data["full_name"]
        owner = workspace or full_name.split("/")[0]
        return Repo(
            provider=self.name,
            id=data.get("uuid") or full_name,
            full_name=full_name,
            name=slug or full_name.split("/")[-1],
            owner=owner,
            description=data.get("description") or "",
            url=((data.get("links") or {}).get("html") or {}).get("href") or "",
            visibility="private" if data.get("is_private") else "public",
            archived=False,
            fork=bool(data.get("parent")),
            language=data.get("language") or None,
            topics=[],
            default_branch=(data.get("mainbranch") or {}).get("name"),
            stars=0,
            created_at=parse_datetime(data.get("created_on")),
            updated_at=parse_datetime(data.get("updated_on")),
            raw=data,
        )

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[Any]:
        """Bitbucket pagina con ``{"values": [...], "next": "<url>"}``."""
        url: str | None = path
        current = params
        while url:
            data = self.request("GET", url, params=current).json()
            yield from data.get("values", [])
            url, current = data.get("next"), None

    def whoami(self) -> str:
        try:
            data = self.request("GET", "/user").json()
            return data.get("username") or data.get("display_name") or data.get("nickname") or "?"
        except (AuthError, ProviderError):
            # Los access tokens de workspace/repositorio no pueden consultar /user.
            if not self.workspaces:
                raise
            self.request("GET", f"/repositories/{self.workspaces[0]}", params={"pagelen": 1})
            return f"workspace:{self.workspaces[0]}"

    def list_repos(self) -> list[Repo]:
        params = {"pagelen": 100, "role": "member"}
        if self.workspaces:
            repos: list[Repo] = []
            for ws in self.workspaces:
                repos.extend(self.to_repo(item) for item in self.paginate(f"/repositories/{ws}", params))
            return repos
        try:
            return [self.to_repo(item) for item in self.paginate("/repositories", params)]
        except AuthError:
            raise
        except ProviderError as exc:
            raise GitKeeperError(
                f"{exc} Configura tu workspace con: gitkeeper config set bitbucket.workspace <workspace>"
            ) from exc

    def get_repo(self, full_name: str) -> Repo:
        return self.to_repo(self.request("GET", f"/repositories/{full_name}").json())

    def update_description(self, repo: Repo, description: str) -> Repo:
        data = self.request("PUT", f"/repositories/{repo.full_name}", json={"description": description}).json()
        return self.to_repo(data)

    def delete_repo(self, repo: Repo) -> None:
        self.request("DELETE", f"/repositories/{repo.full_name}")

    def _src(self, repo: Repo) -> str:
        base = f"/repositories/{repo.full_name}/src"
        return f"{base}/{repo.default_branch}" if repo.default_branch else base

    def list_root(self, repo: Repo) -> list[str]:
        names = []
        for item in self.paginate(f"{self._src(repo)}/", {"pagelen": 100}):
            name = item.get("path", "").rsplit("/", 1)[-1]
            if name:
                names.append(name + ("/" if item.get("type") == "commit_directory" else ""))
        return names

    def read_file(self, repo: Repo, path: str) -> str | None:
        try:
            return self.request("GET", f"{self._src(repo)}/{path}").text
        except NotFoundError:
            return None
