import json

import pytest
from conftest import FakeAI, FakeProvider, make_repo
from rich.console import Console
from typer.testing import CliRunner

from gitkeeper import cli
from gitkeeper.config import Config
from gitkeeper.errors import AuthError
from gitkeeper.service import GitKeeper

runner = CliRunner()


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    monkeypatch.setattr(cli, "console", Console(width=200, color_system=None))
    monkeypatch.setattr(cli, "err_console", Console(width=200, color_system=None))


@pytest.fixture
def env(monkeypatch):
    """Servicio con dos proveedores falsos y una IA falsa."""
    gh = FakeProvider("github", [
        make_repo("app", description="Aplicación principal", language="Python"),
        make_repo("empty"),
        make_repo("old", archived=True),
    ])
    bb = FakeProvider("bitbucket", [make_repo("site", provider="bitbucket", owner="acme")],
                      supports_topics=False, supports_archive=False)
    ai = FakeAI()
    state = {"gh": gh, "bb": bb, "ai": ai, "only": []}

    def factory(config, only=None):
        state["only"].append(only)
        providers = [p for p in (gh, bb) if not only or p.name in only]
        return GitKeeper(providers, ai_factory=lambda: ai, language=str(config.get("general", "language")))

    monkeypatch.setattr(cli, "make_service", factory)
    return state


def run(*args, input=None):
    return runner.invoke(cli.app, list(args), input=input)


def test_version():
    result = run("--version")
    assert result.exit_code == 0 and "gitkeeper" in result.output


def test_list_table(env):
    result = run("list")
    assert result.exit_code == 0, result.output
    assert "alice/app" in result.output and "acme/site" in result.output and "alice/old" in result.output
    assert "Repositorios (4)" in result.output


def test_list_filters_and_json(env):
    result = run("list", "--active", "--json")
    names = [r["full_name"] for r in json.loads(result.output)]
    assert "alice/old" not in names and len(names) == 3
    result = run("list", "--archived", "--json")
    assert [r["full_name"] for r in json.loads(result.output)] == ["alice/old"]
    result = run("list", "--no-description", "--active", "--json", "--sort", "name")
    assert [r["full_name"] for r in json.loads(result.output)] == ["acme/site", "alice/empty"]
    result = run("list", "-p", "bitbucket", "--json")
    assert [r["provider"] for r in json.loads(result.output)] == ["bitbucket"]
    assert env["only"][-1] == ["bitbucket"]
    result = run("list", "--json", "--sort", "name", "--reverse", "-n", "1")
    assert [r["full_name"] for r in json.loads(result.output)] == ["alice/old"]


def test_list_invalid_provider(env):
    result = run("list", "-p", "sourceforge")
    assert result.exit_code == 1 and "Proveedor desconocido" in result.output


def test_list_shows_provider_errors(env):
    env["bb"].fail = AuthError("Bitbucket: credenciales rechazadas")
    result = run("list")
    assert result.exit_code == 0
    assert "credenciales rechazadas" in result.output and "alice/app" in result.output


def test_no_accounts(monkeypatch):
    monkeypatch.setattr(cli, "make_service", lambda config, only=None: GitKeeper([]))
    result = run("list")
    assert result.exit_code == 1 and "auth login" in result.output


def test_recent_latest_search(env):
    assert "Actividad reciente (3)" in run("recent").output
    assert "Actividad reciente (4)" in run("recent", "--all").output
    assert "Últimos creados (1)" in run("latest", "-n", "1").output
    result = run("search", "principal", "--json")
    assert [r["full_name"] for r in json.loads(result.output)] == ["alice/app"]
    assert "No hay repositorios" in run("search", "zzz").output


def test_show(env):
    result = run("show", "app")
    assert result.exit_code == 0 and "github:alice/app" in result.output and "Python" in result.output
    data = json.loads(run("show", "bitbucket:acme/site", "--json").output)
    assert data["provider"] == "bitbucket"
    result = run("show", "nothing")
    assert result.exit_code == 1 and "No se encontró" in result.output


def test_open(env, monkeypatch):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert run("open", "app").exit_code == 0
    assert opened == ["https://github.example/alice/app"]


def test_set_description_and_topics(env):
    result = run("set-description", "app", "Nueva descripción")
    assert result.exit_code == 0
    assert ("description", "alice/app", "Nueva descripción") in env["gh"].calls
    result = run("topics", "app", "cli", "tools")
    assert result.exit_code == 0 and ("topics", "alice/app", ["cli", "tools"]) in env["gh"].calls
    result = run("topics", "site", "x")
    assert result.exit_code == 1 and "no soporta topics" in result.output


# ------------------------------------------------------------------ describe
def test_describe_requires_target(env):
    result = run("describe")
    assert result.exit_code == 1 and "--missing" in result.output


def test_describe_yes_applies(env):
    result = run("describe", "app", "--yes")
    assert result.exit_code == 0, result.output
    assert ("description", "alice/app", "Generated description") in env["gh"].calls
    assert ("topics", "alice/app", ["cli", "python"]) in env["gh"].calls
    assert "actualizado" in result.output


def test_describe_no_topics(env):
    run("describe", "app", "--yes", "--no-topics")
    assert not any(c[0] == "topics" for c in env["gh"].calls)


def test_describe_dry_run(env):
    result = run("describe", "app", "--dry-run")
    assert result.exit_code == 0 and "Generated description" in result.output
    assert not any(c[0] == "description" for c in env["gh"].calls)


def test_describe_interactive_apply(env):
    result = run("describe", "app", input="a\n")
    assert result.exit_code == 0, result.output
    assert ("description", "alice/app", "Generated description") in env["gh"].calls


def test_describe_interactive_edit_then_apply(env):
    result = run("describe", "app", input="e\nMi texto\ncli, web\na\n")
    assert result.exit_code == 0, result.output
    assert ("description", "alice/app", "Mi texto") in env["gh"].calls
    assert ("topics", "alice/app", ["cli", "web"]) in env["gh"].calls


def test_describe_interactive_regenerate_and_skip(env):
    env["ai"].responses = ['{"description": "Uno", "topics": []}', '{"description": "Dos", "topics": []}']
    result = run("describe", "app", input="r\ns\n")
    assert result.exit_code == 0
    assert "Dos" in result.output and "Saltado" in result.output
    assert len(env["ai"].prompts) == 2
    assert not any(c[0] == "description" for c in env["gh"].calls)


def test_describe_invalid_choice_reprompts(env):
    result = run("describe", "app", input="z\nq\n")
    assert result.exit_code == 0 and "Opción no válida" in result.output


def test_describe_missing_bulk(env):
    result = run("describe", "--missing", "--yes")
    assert result.exit_code == 0, result.output
    assert ("description", "alice/empty", "Generated description") in env["gh"].calls
    assert ("description", "acme/site", "Generated description") in env["bb"].calls
    # Los archivados y los que ya tienen descripción no se tocan.
    assert not any(c[1] in ("alice/old", "alice/app") for c in env["gh"].calls if c[0] == "description")
    # Bitbucket no soporta topics: no se intentan.
    assert not any(c[0] == "topics" for c in env["bb"].calls)
    assert "Actualizados: 2" in result.output


def test_describe_bulk_continues_after_write_error(env, monkeypatch):
    def failing(repo, description):
        raise AuthError("GitHub: permiso denegado (403).")

    monkeypatch.setattr(env["gh"], "update_description", failing)
    result = run("describe", "--missing", "--yes")
    assert "permiso denegado" in result.output
    assert ("description", "acme/site", "Generated description") in env["bb"].calls
    assert "Actualizados: 1" in result.output and "Errores: 1" in result.output
    assert result.exit_code == 0


def test_describe_missing_quit_stops(env):
    result = run("describe", "--missing", input="q\n")
    assert result.exit_code == 0
    assert not any(c[0] == "description" for p in ("gh", "bb") for c in env[p].calls)


def test_describe_missing_none(env):
    for repo in env["gh"].repos.values():
        repo.description = "x"
    for repo in env["bb"].repos.values():
        repo.description = "x"
    assert "tienen descripción" in run("describe", "--missing").output


def test_describe_ai_error_reports(env):
    env["ai"].responses = ["not json"]
    result = run("describe", "app", "--yes")
    assert result.exit_code == 1 and "JSON" in result.output


def test_describe_lang_option(env):
    run("describe", "app", "--dry-run", "--lang", "en")
    assert "English" in env["ai"].prompts[-1][1]


# ------------------------------------------------------------ archive/delete
def test_archive_confirm_and_yes(env):
    result = run("archive", "app", input="n\n")
    assert result.exit_code == 0 and "Saltado" in result.output
    assert not any(c[0] == "archive" for c in env["gh"].calls)
    result = run("archive", "app", "--yes")
    assert result.exit_code == 0 and ("archive", "alice/app", True) in env["gh"].calls


def test_archive_already_archived(env):
    result = run("archive", "old", "-y")
    assert "ya está archivado" in result.output


def test_unarchive(env):
    result = run("unarchive", "old", "-y")
    assert result.exit_code == 0 and ("archive", "alice/old", False) in env["gh"].calls


def test_archive_unsupported_provider(env):
    result = run("archive", "site", "-y")
    assert result.exit_code == 1 and "no permite archivar" in result.output


def test_delete_simple_confirmation(env):
    result = run("delete", "app", input="\n")  # por defecto: no
    assert result.exit_code == 1 and "Cancelado" in result.output
    assert "alice/app" in env["gh"].repos
    result = run("delete", "app", input="y\n")
    assert result.exit_code == 0, result.output
    assert "alice/app" not in env["gh"].repos


def test_delete_yes(env):
    assert run("delete", "site", "--yes").exit_code == 0
    assert "acme/site" not in env["bb"].repos


# --------------------------------------------------------------------- auth
class LoginProvider:
    label = "GitHub"

    def __init__(self, user="alice", error=None):
        self.user, self.error, self.closed = user, error, False

    def whoami(self):
        if self.error:
            raise self.error
        return self.user

    def close(self):
        self.closed = True


def test_auth_login_verifies_and_saves(monkeypatch):
    built = []

    def fake_build(name, config, token, username=""):
        built.append((name, token, username))
        return LoginProvider()

    monkeypatch.setattr(cli, "build_provider", fake_build)
    result = run("auth", "login", "github", input="ghp_secret\n")
    assert result.exit_code == 0, result.output
    assert "alice" in result.output
    assert built == [("github", "ghp_secret", "")]
    assert Config.load().get("github", "token") == "ghp_secret"
    assert "ghp_secret" not in result.output


def test_auth_login_rejected_token_not_saved(monkeypatch):
    monkeypatch.setattr(cli, "build_provider",
                        lambda *a, **k: LoginProvider(error=AuthError("GitHub: credenciales rechazadas (401).")))
    result = run("auth", "login", "github", "--token", "bad")
    assert result.exit_code == 1 and "rechazadas" in result.output
    assert Config.load().get("github", "token") == ""


def test_auth_login_bitbucket_prompts_username(monkeypatch):
    built = []
    monkeypatch.setattr(cli, "build_provider", lambda name, config, token, username="": built.append(username) or LoginProvider())
    result = run("auth", "login", "bitbucket", "-w", "acme", input="me@example.com\ntok\n")
    assert result.exit_code == 0, result.output
    config = Config.load()
    assert config.get("bitbucket", "username") == "me@example.com"
    assert config.get("bitbucket", "workspace") == "acme"
    assert built == ["me@example.com"]


def test_auth_login_self_hosted_url_no_verify():
    result = run("auth", "login", "gitlab", "--token", "glpat", "--url", "https://git.corp/api/v4", "--no-verify")
    assert result.exit_code == 0, result.output
    config = Config.load()
    assert config.get("gitlab", "api_url") == "https://git.corp/api/v4"
    assert config.get("gitlab", "token") == "glpat"


def test_auth_login_prompts_provider(monkeypatch):
    monkeypatch.setattr(cli, "build_provider", lambda *a, **k: LoginProvider())
    result = run("auth", "login", input="gitlab\ntok\n")
    assert result.exit_code == 0, result.output
    assert Config.load().get("gitlab", "token") == "tok"


def test_auth_login_env_warning(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env")
    result = run("auth", "login", "github", "--token", "t", "--no-verify")
    assert "GITHUB_TOKEN" in result.output


def test_auth_status_and_logout(monkeypatch):
    monkeypatch.setattr(cli, "build_provider", lambda *a, **k: LoginProvider("bob"))
    result = run("auth", "status")
    assert "sin configurar" in result.output and "auth login" in result.output
    run("auth", "login", "github", "--token", "t", "--no-verify")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    result = run("auth", "status")
    assert "bob" in result.output and "token desde config" in result.output
    assert "Claude (claude-opus-5)" in result.output
    assert "token desde config" in run("auth", "status", "--offline").output
    result = run("auth", "logout", "github")
    assert result.exit_code == 0
    assert Config.load().get("github", "token") == ""


def test_auth_status_bad_token(monkeypatch):
    monkeypatch.setattr(cli, "build_provider", lambda *a, **k: LoginProvider(error=AuthError("rechazadas")))
    monkeypatch.setenv("GITLAB_TOKEN", "x")
    assert "rechazadas" in run("auth", "status").output


# ------------------------------------------------------------------- config
def test_config_set_show_unset():
    assert run("config", "set", "ai.provider", "gemini").exit_code == 0
    assert run("config", "set", "github.token", "ghp_abcdefghijkl").exit_code == 0
    result = run("config", "show")
    assert "provider = gemini" in result.output
    assert "ghp_abcdefghijkl" not in result.output and "ghp_…ijkl" in result.output
    assert run("config", "unset", "ai.provider").exit_code == 0
    assert Config.load().get("ai", "provider") == "auto"
    result = run("config", "set", "ai.provider", "bogus")
    assert result.exit_code == 1 and "inválido" in result.output


def test_config_path(tmp_path):
    assert run("config", "path").output.strip() == str(tmp_path / "config.toml")


def test_no_args_launches_tui(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "tui", lambda: called.append(True))
    assert run().exit_code == 0
    assert called == [True]
