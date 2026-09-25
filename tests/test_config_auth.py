import subprocess

import pytest

from gitkeeper import auth
from gitkeeper.auth import _gh_cli_token as real_gh_cli_token
from gitkeeper.auth import resolve_credentials
from gitkeeper.config import Config, default_config_path, mask_secret
from gitkeeper.errors import ConfigError
from gitkeeper.providers import BitbucketProvider, GitHubProvider, GitLabProvider, build_provider, build_providers


def test_default_path_from_env(tmp_path):
    assert default_config_path() == tmp_path / "config.toml"


def test_load_missing_file_uses_defaults(tmp_path):
    config = Config.load(tmp_path / "nope.toml")
    assert config.get("gitlab", "api_url") == "https://gitlab.com/api/v4"
    assert config.get("ai", "provider") == "auto"


def test_save_and_reload_roundtrip(config):
    config.set("github.token", "abc")
    config.set("ai.provider", "gemini")
    config.save()
    loaded = Config.load(config.path)
    assert loaded.get("github", "token") == "abc"
    assert loaded.get("ai", "provider") == "gemini"
    # Se conservan los valores por defecto no guardados explícitamente.
    assert loaded.get("general", "language") == "es"


def test_invalid_toml(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("[github\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)


@pytest.mark.parametrize("key", ["nokey", "a.b.c", "foo.token", "github.nope"])
def test_set_rejects_unknown_keys(config, key):
    with pytest.raises(ConfigError):
        config.set(key, "x")


def test_set_validates_values(config):
    with pytest.raises(ConfigError):
        config.set("ai.provider", "chatgpt")
    with pytest.raises(ConfigError):
        config.set("gitlab.api_url", "gitlab.local")
    config.set("gitlab.api_url", "https://git.corp/api/v4")
    assert config.get("gitlab", "api_url") == "https://git.corp/api/v4"


def test_unset_restores_default(config):
    config.set("github.api_url", "https://ghe/api/v3")
    config.unset("github.api_url")
    assert config.get("github", "api_url") == "https://api.github.com"


def test_redacted_hides_secrets(config):
    config.set("github.token", "ghp_1234567890")
    config.set("ai.api_key", "short")
    red = config.redacted()
    assert red["github"]["token"] == "ghp_…7890"
    assert red["ai"]["api_key"] == "*****"
    assert config.get("github", "token") == "ghp_1234567890"


def test_mask_secret():
    assert mask_secret("abcdefghijk") == "abcd…hijk"
    assert mask_secret("abc") == "***"


# ------------------------------------------------------------------ auth
def test_no_credentials(config):
    assert resolve_credentials("github", config) is None


def test_env_has_priority_over_config(config, monkeypatch):
    config.set("github.token", "from-config")
    assert resolve_credentials("github", config).source == "config"
    monkeypatch.setenv("GH_TOKEN", "from-env")
    creds = resolve_credentials("github", config)
    assert creds.token == "from-env" and creds.source == "env:GH_TOKEN"
    monkeypatch.setenv("GITKEEPER_GITHUB_TOKEN", "own")
    assert resolve_credentials("github", config).token == "own"


def test_gh_cli_fallback(config, monkeypatch):
    monkeypatch.setattr(auth, "_gh_cli_token", lambda: "gho_cli")
    creds = resolve_credentials("github", config)
    assert creds.token == "gho_cli" and creds.source == "gh cli"
    assert resolve_credentials("github", config, use_cli=False) is None
    # La CLI de gh solo se usa para GitHub.
    assert resolve_credentials("gitlab", config) is None


def test_gh_cli_token_invocation(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        auth.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="gho_123\n", stderr=""),
    )
    assert real_gh_cli_token() == "gho_123"
    monkeypatch.setattr(
        auth.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="no")
    )
    assert real_gh_cli_token() is None
    monkeypatch.setattr(auth.shutil, "which", lambda name: None)
    assert real_gh_cli_token() is None


def test_bitbucket_username(config, monkeypatch):
    config.set("bitbucket.token", "tok")
    config.set("bitbucket.username", "cfg@example.com")
    assert resolve_credentials("bitbucket", config).username == "cfg@example.com"
    monkeypatch.setenv("BITBUCKET_EMAIL", "env@example.com")
    assert resolve_credentials("bitbucket", config).username == "env@example.com"


# ------------------------------------------------------------- providers
def test_build_providers_only_with_credentials(config, monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", "gl")
    config.set("bitbucket.token", "bb")
    config.set("bitbucket.workspace", "acme")
    providers = build_providers(config)
    assert [type(p) for p in providers] == [GitLabProvider, BitbucketProvider]
    assert providers[1].workspaces == ["acme"]
    assert [p.name for p in build_providers(config, only=["gitlab"])] == ["gitlab"]


def test_build_provider_uses_config(config):
    config.set("github.api_url", "https://ghe.corp/api/v3")
    config.set("github.affiliation", "owner")
    config.set("gitlab.scope", "owned")
    gh = build_provider("github", config, "t")
    assert isinstance(gh, GitHubProvider)
    assert gh.api_url == "https://ghe.corp/api/v3" and gh.affiliation == "owner"
    assert build_provider("gitlab", config, "t").scope == "owned"
