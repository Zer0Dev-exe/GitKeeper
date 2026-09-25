"""Proveedor GitHub (REST API v3). Compatible con GitHub Enterprise cambiando ``api_url``."""

from __future__ import annotations

from typing import Any

import httpx

from gitkeeper.errors import NotFoundError, ProviderError
from gitkeeper.models import Repo, parse_datetime
from gitkeeper.providers.base import Provider, normalize_topics, to_percentages


class GitHubProvider(Provider):
    name = "github"
    label = "GitHub"

    def __init__(self, token: str, api_url: str = "https://api.github.com",
                 affiliation: str = "owner,organization_member", **kwargs: Any):
        self.affiliation = affiliation or "owner,organization_member"
        super().__init__(token, api_url, **kwargs)

    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _raise_for(self, response: httpx.Response) -> None:
        message = self.error_message(response)
        # Token fine-grained sin el permiso necesario: decir cuál falta.
        if response.status_code == 403 and "personal access token" in message.lower():
            path = response.request.url.path
            if response.request.method == "GET" and ("/contents" in path or path.endswith("/readme")):
                needed = "'Contents: Read-only' (para leer el README y los ficheros)"
            else:
                needed = "'Administration: Read and write' (para cambiar descripción/topics, archivar o borrar)"
            raise ProviderError(
                f"GitHub: tu token no tiene el permiso {needed}. Añádelo en "
                "https://github.com/settings/personal-access-tokens (el mismo token sigue valiendo), "
                "o usa un token classic con los scopes 'repo' y 'delete_repo'.",
                403,
            )
        super()._raise_for(response)

    def to_repo(self, data: dict[str, Any]) -> Repo:
        pushed = parse_datetime(data.get("pushed_at"))
        updated = parse_datetime(data.get("updated_at"))
        # "Actividad" = lo más reciente entre el último push y el último cambio de metadatos.
        activity = max((d for d in (pushed, updated) if d), default=None)
        visibility = data.get("visibility") or ("private" if data.get("private") else "public")
        return Repo(
            provider=self.name,
            id=str(data.get("id", "")),
            full_name=data["full_name"],
            name=data.get("name") or data["full_name"].split("/")[-1],
            owner=(data.get("owner") or {}).get("login") or data["full_name"].split("/")[0],
            description=data.get("description") or "",
            url=data.get("html_url") or "",
            visibility=visibility,
            archived=bool(data.get("archived")),
            fork=bool(data.get("fork")),
            language=data.get("language"),
            topics=list(data.get("topics") or []),
            default_branch=data.get("default_branch"),
            stars=int(data.get("stargazers_count") or 0),
            created_at=parse_datetime(data.get("created_at")),
            updated_at=activity,
            raw=data,
        )

    def whoami(self) -> str:
        return self.request("GET", "/user").json()["login"]

    def list_repos(self) -> list[Repo]:
        params = {"per_page": 100, "affiliation": self.affiliation, "sort": "updated"}
        return [self.to_repo(item) for item in self.paginate("/user/repos", params)]

    def get_repo(self, full_name: str) -> Repo:
        return self.to_repo(self.request("GET", f"/repos/{full_name}").json())

    def update_description(self, repo: Repo, description: str) -> Repo:
        data = self.request("PATCH", f"/repos/{repo.full_name}", json={"description": description}).json()
        return self.to_repo(data)

    def set_topics(self, repo: Repo, topics: list[str]) -> Repo:
        names = normalize_topics(topics)
        self.request("PUT", f"/repos/{repo.full_name}/topics", json={"names": names})
        repo.topics = names
        return repo

    def set_archived(self, repo: Repo, archived: bool) -> Repo:
        data = self.request("PATCH", f"/repos/{repo.full_name}", json={"archived": archived}).json()
        return self.to_repo(data)

    def delete_repo(self, repo: Repo) -> None:
        self.request("DELETE", f"/repos/{repo.full_name}")

    def get_languages(self, repo: Repo) -> dict[str, float]:
        # GitHub devuelve bytes de código por lenguaje.
        return to_percentages(self.request("GET", f"/repos/{repo.full_name}/languages").json() or {})

    def list_root(self, repo: Repo) -> list[str]:
        items = self.request("GET", f"/repos/{repo.full_name}/contents").json()
        if not isinstance(items, list):
            return []
        return [item["name"] + ("/" if item.get("type") == "dir" else "") for item in items]

    def read_file(self, repo: Repo, path: str) -> str | None:
        try:
            response = self.request(
                "GET",
                f"/repos/{repo.full_name}/contents/{path}",
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except NotFoundError:
            return None
        return response.text

    def get_readme(self, repo: Repo) -> str | None:
        try:
            response = self.request(
                "GET",
                f"/repos/{repo.full_name}/readme",
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except NotFoundError:
            return None
        return response.text
