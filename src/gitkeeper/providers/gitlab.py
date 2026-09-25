"""Proveedor GitLab (REST API v4). Funciona con gitlab.com y con instancias self-hosted."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from gitkeeper.errors import NotFoundError
from gitkeeper.models import Repo, parse_datetime
from gitkeeper.providers.base import Provider, normalize_topics, to_percentages


def encode_path(path: str) -> str:
    """GitLab exige las rutas (``grupo/proyecto``, ``dir/fichero``) con '/' codificado como %2F."""
    return quote(path, safe="")


class GitLabProvider(Provider):
    name = "gitlab"
    label = "GitLab"
    lists_language = False

    def __init__(self, token: str, api_url: str = "https://gitlab.com/api/v4",
                 scope: str = "member", **kwargs: Any):
        self.scope = scope or "member"
        super().__init__(token, api_url, **kwargs)

    def auth_headers(self) -> dict[str, str]:
        # GitLab acepta PATs y tokens OAuth como Bearer.
        return {"Authorization": f"Bearer {self.token}"}

    def to_repo(self, data: dict[str, Any]) -> Repo:
        full_name = data["path_with_namespace"]
        namespace = data.get("namespace") or {}
        return Repo(
            provider=self.name,
            id=str(data["id"]),
            full_name=full_name,
            name=data.get("path") or full_name.split("/")[-1],
            owner=namespace.get("full_path") or full_name.rsplit("/", 1)[0],
            description=data.get("description") or "",
            url=data.get("web_url") or "",
            visibility=data.get("visibility") or "private",
            archived=bool(data.get("archived")),
            fork=bool(data.get("forked_from_project")),
            language=None,
            topics=list(data.get("topics") or data.get("tag_list") or []),
            default_branch=data.get("default_branch"),
            stars=int(data.get("star_count") or 0),
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("last_activity_at") or data.get("updated_at")),
            raw=data,
        )

    def _project(self, repo: Repo) -> str:
        return f"/projects/{repo.id}"

    def next_page_fallback(self, response: httpx.Response, path: str, params: dict[str, Any] | None) -> str | None:
        # Si no hay header Link (algunos proxies lo eliminan) usamos X-Next-Page.
        next_page = response.headers.get("X-Next-Page", "").strip()
        if not next_page:
            return None
        return str(httpx.URL(self.url(path), params={**(params or {}), "page": next_page}))

    def whoami(self) -> str:
        return self.request("GET", "/user").json()["username"]

    def list_repos(self) -> list[Repo]:
        params: dict[str, Any] = {"per_page": 100, "order_by": "last_activity_at", "sort": "desc"}
        if self.scope == "owned":
            params["owned"] = "true"
        else:
            params["membership"] = "true"
        return [self.to_repo(item) for item in self.paginate("/projects", params)]

    def get_repo(self, full_name: str) -> Repo:
        return self.to_repo(self.request("GET", f"/projects/{encode_path(full_name)}").json())

    def update_description(self, repo: Repo, description: str) -> Repo:
        data = self.request("PUT", self._project(repo), json={"description": description}).json()
        return self.to_repo(data)

    def set_topics(self, repo: Repo, topics: list[str]) -> Repo:
        data = self.request("PUT", self._project(repo), json={"topics": normalize_topics(topics)}).json()
        return self.to_repo(data)

    def set_archived(self, repo: Repo, archived: bool) -> Repo:
        action = "archive" if archived else "unarchive"
        data = self.request("POST", f"{self._project(repo)}/{action}").json()
        return self.to_repo(data)

    def delete_repo(self, repo: Repo) -> None:
        # Devuelve 202: en gitlab.com puede quedar "pendiente de borrado" unos días.
        self.request("DELETE", self._project(repo))

    def get_languages(self, repo: Repo) -> dict[str, float]:
        # GitLab devuelve porcentajes directamente.
        return to_percentages(self.request("GET", f"{self._project(repo)}/languages").json() or {})

    def list_root(self, repo: Repo) -> list[str]:
        params: dict[str, Any] = {"per_page": 100}
        if repo.default_branch:
            params["ref"] = repo.default_branch
        items = self.request("GET", f"{self._project(repo)}/repository/tree", params=params).json()
        return [item["name"] + ("/" if item.get("type") == "tree" else "") for item in items]

    def read_file(self, repo: Repo, path: str) -> str | None:
        params = {"ref": repo.default_branch or "HEAD"}
        try:
            response = self.request(
                "GET", f"{self._project(repo)}/repository/files/{encode_path(path)}/raw", params=params
            )
        except NotFoundError:
            return None
        return response.text
