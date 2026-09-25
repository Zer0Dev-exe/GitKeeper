"""Interfaz de línea de comandos (Typer)."""

from __future__ import annotations

import functools
import json
import os
import sys
import webbrowser
from typing import Any, Callable, Optional

import typer
from rich.console import Console
from rich.markup import escape

from gitkeeper import __version__
from gitkeeper.ai import detect_provider, get_ai_provider
from gitkeeper.auth import TOKEN_ENV_VARS, TOKEN_HELP, resolve_credentials
from gitkeeper.config import PROVIDERS, Config
from gitkeeper.errors import GitKeeperError
from gitkeeper.models import Repo, Suggestion
from gitkeeper.providers import LABELS, build_provider, build_providers
from gitkeeper.render import print_errors, repo_panel, repos_table, suggestion_panel
from gitkeeper.service import SORT_KEYS, FetchResult, GitKeeper, filter_repos, sort_repos

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="gitkeeper",
    help="Gestiona tus repositorios de GitHub, GitLab y Bitbucket, con descripciones generadas por IA.",
    no_args_is_help=False,
    add_completion=True,
    rich_markup_mode="rich",
)
auth_app = typer.Typer(help="Inicia sesión en tus plataformas y comprueba las credenciales.")
config_app = typer.Typer(help="Consulta y modifica la configuración.")
app.add_typer(auth_app, name="auth")
app.add_typer(config_app, name="config")

ProviderOpt = Optional[str]


# ---------------------------------------------------------------------------
# Infraestructura
# ---------------------------------------------------------------------------
def load_config() -> Config:
    return Config.load()


def make_service(config: Config, only: list[str] | None = None) -> GitKeeper:
    """Crea el servicio. Los tests sustituyen esta función."""
    return GitKeeper(
        build_providers(config, only=only),
        ai_factory=lambda: get_ai_provider(config),
        language=str(config.get("general", "language", "es")),
    )


def handle_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except GitKeeperError as exc:
            err_console.print(f"[bold red]Error:[/] {escape(str(exc))}")
            raise typer.Exit(1) from exc
        except KeyboardInterrupt:
            err_console.print("\n[yellow]Cancelado.[/]")
            raise typer.Exit(130)

    return wrapper


def _check_provider(provider: str | None) -> list[str] | None:
    if provider is None:
        return None
    if provider not in PROVIDERS:
        raise GitKeeperError(f"Proveedor desconocido '{provider}'. Válidos: {', '.join(PROVIDERS)}.")
    return [provider]


def _choose(text: str, choices: list[str], default: str) -> str:
    """Pregunta hasta obtener una de las opciones (sin distinguir mayúsculas)."""
    while True:
        answer = str(typer.prompt(text, default=default)).strip().lower()
        if answer in choices:
            return answer
        console.print(f"[yellow]Opción no válida. Elige entre: {', '.join(choices)}.[/]")


def _print_json(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _show_repos(result: FetchResult, as_json: bool, title: str, date_field: str = "updated") -> None:
    if as_json:
        _print_json([r.to_dict() for r in result.repos])
        return
    print_errors(err_console, result.errors)
    if not result.repos:
        console.print("[dim]No hay repositorios que mostrar.[/]")
        return
    console.print(repos_table(result.repos, title=f"{title} ({len(result.repos)})", date_field=date_field))


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"gitkeeper {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", callback=version_callback, is_eager=True,
                                 help="Muestra la versión."),
) -> None:
    """Sin subcomando abre la interfaz interactiva (TUI)."""
    if ctx.invoked_subcommand is None:
        tui()


# ---------------------------------------------------------------------------
# Listados
# ---------------------------------------------------------------------------
@app.command("list")
@handle_errors
def list_cmd(
    provider: ProviderOpt = typer.Option(None, "--provider", "-p", help="github, gitlab o bitbucket."),
    archived: Optional[bool] = typer.Option(None, "--archived/--active",
                                            help="Solo archivados / solo activos (por defecto, todos)."),
    visibility: Optional[str] = typer.Option(None, "--visibility", "-v", help="public, private o internal."),
    language: Optional[str] = typer.Option(None, "--language", "-l", help="Filtra por lenguaje."),
    forks: Optional[bool] = typer.Option(None, "--forks/--no-forks", help="Solo forks / sin forks."),
    no_description: bool = typer.Option(False, "--no-description", help="Solo repos sin descripción."),
    sort: str = typer.Option("updated", "--sort", "-s", help=f"Orden: {', '.join(SORT_KEYS)}."),
    reverse: bool = typer.Option(False, "--reverse", "-r", help="Invierte el orden."),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", min=1, help="Máximo de resultados."),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON."),
) -> None:
    """Lista tus repositorios."""
    only = _check_provider(provider)
    service = make_service(load_config(), only)
    try:
        fetched = service.fetch_all(only)
    finally:
        service.close()
    repos = filter_repos(fetched.repos, archived=archived, visibility=visibility, language=language, forks=forks)
    if no_description:
        repos = [r for r in repos if not r.description.strip()]
    repos = sort_repos(repos, sort)
    if reverse:
        repos.reverse()
    if limit:
        repos = repos[:limit]
    _show_repos(FetchResult(repos, fetched.errors), as_json, "Repositorios",
                "created" if sort == "created" else "updated")


@app.command()
@handle_errors
def recent(
    limit: int = typer.Option(10, "--limit", "-n", min=1, help="Cuántos mostrar."),
    include_archived: bool = typer.Option(False, "--all", "-a", help="Incluye archivados."),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON."),
) -> None:
    """Repositorios con actividad más reciente."""
    service = make_service(load_config())
    try:
        result = service.recent(limit, include_archived)
    finally:
        service.close()
    _show_repos(result, as_json, "Actividad reciente")


@app.command()
@handle_errors
def latest(
    limit: int = typer.Option(10, "--limit", "-n", min=1, help="Cuántos mostrar."),
    include_archived: bool = typer.Option(False, "--all", "-a", help="Incluye archivados."),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON."),
) -> None:
    """Últimos repositorios creados."""
    service = make_service(load_config())
    try:
        result = service.latest(limit, include_archived)
    finally:
        service.close()
    _show_repos(result, as_json, "Últimos creados", date_field="created")


@app.command()
@handle_errors
def search(
    query: list[str] = typer.Argument(..., help="Palabras a buscar en nombre, descripción, lenguaje y topics."),
    active: bool = typer.Option(False, "--active", help="Excluye archivados."),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON."),
) -> None:
    """Busca repositorios."""
    text = " ".join(query)
    service = make_service(load_config())
    try:
        result = service.search(text, include_archived=not active)
    finally:
        service.close()
    _show_repos(result, as_json, f"Resultados para «{text}»")


@app.command()
@handle_errors
def show(
    repo: str = typer.Argument(..., help="nombre, owner/nombre, proveedor:owner/nombre o URL."),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON."),
) -> None:
    """Muestra el detalle de un repositorio."""
    service = make_service(load_config())
    try:
        found = service.resolve(repo)
        languages = service.languages(found)
    finally:
        service.close()
    if as_json:
        _print_json({**found.to_dict(), "languages": languages})
    else:
        console.print(repo_panel(found, languages))


@app.command("open")
@handle_errors
def open_cmd(repo: str = typer.Argument(..., help="Repositorio a abrir en el navegador.")) -> None:
    """Abre el repositorio en el navegador."""
    service = make_service(load_config())
    try:
        found = service.resolve(repo)
    finally:
        service.close()
    if not found.url:
        raise GitKeeperError("El repositorio no tiene URL web.")
    console.print(f"Abriendo {escape(found.url)}")
    webbrowser.open(found.url)


# ---------------------------------------------------------------------------
# Descripciones
# ---------------------------------------------------------------------------
def _review(service: GitKeeper, repo: Repo, suggestion: Suggestion, with_topics: bool) -> Repo | None | bool:
    """Pide confirmación. Devuelve el repo actualizado, None si se salta, False para salir."""
    while True:
        console.print(suggestion_panel(repo, suggestion, show_topics=with_topics))
        choice = _choose("¿[a]plicar, [e]ditar, [r]egenerar, [s]altar o [q] salir?", ["a", "e", "r", "s", "q"], "a")
        if choice == "a":
            return service.update_description(repo, suggestion.description, suggestion.topics if with_topics else None)
        if choice == "e":
            text = typer.prompt("Descripción", default=suggestion.description).strip()
            if text:
                suggestion = Suggestion(text, suggestion.topics)
            if with_topics:
                topics = typer.prompt("Topics (separados por comas)", default=", ".join(suggestion.topics))
                suggestion = Suggestion(suggestion.description, [t.strip() for t in topics.split(",") if t.strip()])
            continue
        if choice == "r":
            with console.status("Generando otra propuesta…"):
                suggestion = service.suggest(repo)
            continue
        if choice == "s":
            return None
        return False


@app.command()
@handle_errors
def describe(
    repos: Optional[list[str]] = typer.Argument(None, help="Repositorios a describir."),
    missing: bool = typer.Option(False, "--missing", "-m", help="Todos los repos (activos) sin descripción."),
    provider: ProviderOpt = typer.Option(None, "--provider", "-p", help="Con --missing: limita a un proveedor."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Aplica sin preguntar."),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Solo muestra la propuesta."),
    topics: bool = typer.Option(True, "--topics/--no-topics", help="Actualiza también los topics."),
    lang: Optional[str] = typer.Option(None, "--lang", help="Idioma de la descripción (es, en...)."),
) -> None:
    """Genera la descripción de uno o varios repositorios con IA y la actualiza."""
    if not repos and not missing:
        raise GitKeeperError("Indica uno o más repositorios, o usa --missing.")
    config = load_config()
    only = _check_provider(provider)
    service = make_service(config)
    if lang:
        service.language = lang
    try:
        if missing:
            fetched = service.fetch_all(only)
            print_errors(err_console, fetched.errors)
            targets = sort_repos(
                [r for r in filter_repos(fetched.repos, archived=False) if not r.description.strip()], "name"
            )
            if not targets:
                console.print("[green]Todos tus repositorios activos tienen descripción.[/]")
                return
            console.print(f"[bold]{len(targets)}[/] repositorios sin descripción.")
        else:
            targets = [service.resolve(ref) for ref in repos or []]

        console.print(f"[dim]IA: {escape(service.ai.describe())}[/]")
        updated = skipped = failed = 0
        for repo in targets:
            provider_obj = service.provider_for(repo)
            with_topics = topics and provider_obj.supports_topics
            try:
                with console.status(f"Analizando {escape(repo.ref)}…"):
                    suggestion = service.suggest(repo)
            except GitKeeperError as exc:
                err_console.print(f"[red]✗ {escape(repo.ref)}:[/] {escape(str(exc))}")
                failed += 1
                continue

            if dry_run:
                console.print(suggestion_panel(repo, suggestion, show_topics=with_topics))
                continue
            result: Repo | None | bool
            try:
                if yes:
                    console.print(suggestion_panel(repo, suggestion, show_topics=with_topics))
                    result = service.update_description(
                        repo, suggestion.description, suggestion.topics if with_topics else None
                    )
                else:
                    result = _review(service, repo, suggestion, with_topics)
            except GitKeeperError as exc:
                err_console.print(f"[red]✗ {escape(repo.ref)}:[/] {escape(str(exc))}")
                failed += 1
                continue
            if result is False:
                break
            if result is None:
                skipped += 1
                console.print("[dim]Saltado.[/]")
            else:
                updated += 1
                console.print(f"[green]✓ {escape(repo.ref)} actualizado.[/]")
    finally:
        service.close()
    if not dry_run and len(targets) > 1:
        console.print(f"\nActualizados: [green]{updated}[/] · Saltados: {skipped} · Errores: [red]{failed}[/]")
    if failed and not updated and not dry_run:
        raise typer.Exit(1)


@app.command("set-description")
@handle_errors
def set_description(
    repo: str = typer.Argument(..., help="Repositorio."),
    description: str = typer.Argument(..., help="Nueva descripción (\"\" para vaciarla)."),
) -> None:
    """Cambia la descripción manualmente."""
    service = make_service(load_config())
    try:
        updated = service.update_description(service.resolve(repo), description)
    finally:
        service.close()
    console.print(f"[green]✓ {escape(updated.ref)}:[/] {escape(updated.description or '(vacía)')}")


@app.command("topics")
@handle_errors
def topics_cmd(
    repo: str = typer.Argument(..., help="Repositorio."),
    names: list[str] = typer.Argument(..., help="Topics (reemplazan a los actuales)."),
) -> None:
    """Reemplaza los topics de un repositorio (GitHub y GitLab)."""
    service = make_service(load_config())
    try:
        found = service.resolve(repo)
        provider = service.provider_for(found)
        if not provider.supports_topics:
            raise GitKeeperError(f"{provider.label} no soporta topics.")
        updated = provider.set_topics(found, names)
    finally:
        service.close()
    console.print(f"[green]✓ {escape(updated.ref)}:[/] {escape(', '.join(updated.topics) or '(ninguno)')}")


# ---------------------------------------------------------------------------
# Archivar / borrar
# ---------------------------------------------------------------------------
def _set_archived(refs: list[str], archived: bool, yes: bool) -> None:
    verb = "archivar" if archived else "desarchivar"
    service = make_service(load_config())
    errors = 0
    try:
        for ref in refs:
            try:
                repo = service.resolve(ref)
                if repo.archived == archived:
                    console.print(f"[dim]{escape(repo.ref)} ya está {'archivado' if archived else 'activo'}.[/]")
                    continue
                if not yes and not typer.confirm(f"¿{verb.capitalize()} {repo.ref}?", default=True):
                    console.print("[dim]Saltado.[/]")
                    continue
                updated = service.set_archived(repo, archived)
                console.print(f"[green]✓ {escape(updated.ref)} {'archivado' if archived else 'desarchivado'}.[/]")
            except GitKeeperError as exc:
                errors += 1
                err_console.print(f"[red]✗ {escape(ref)}:[/] {escape(str(exc))}")
    finally:
        service.close()
    if errors:
        raise typer.Exit(1)


@app.command()
@handle_errors
def archive(
    repos: list[str] = typer.Argument(..., help="Repositorios a archivar."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No pedir confirmación."),
) -> None:
    """Archiva repositorios (solo lectura)."""
    _set_archived(repos, True, yes)


@app.command()
@handle_errors
def unarchive(
    repos: list[str] = typer.Argument(..., help="Repositorios a desarchivar."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No pedir confirmación."),
) -> None:
    """Desarchiva repositorios."""
    _set_archived(repos, False, yes)


@app.command()
@handle_errors
def delete(
    repo: str = typer.Argument(..., help="Repositorio a borrar."),
    yes: bool = typer.Option(False, "--yes", help="No pedir confirmación (¡cuidado!)."),
) -> None:
    """Borra un repositorio. [bold red]No se puede deshacer.[/]"""
    service = make_service(load_config())
    try:
        found = service.resolve(repo)
        console.print(repo_panel(found))
        if not yes:
            console.print("[bold red]Esta acción es irreversible.[/]")
            if not typer.confirm(f"¿Borrar {found.ref}?", default=False):
                console.print("[yellow]Cancelado. No se ha borrado nada.[/]")
                raise typer.Exit(1)
        service.delete(found)
    finally:
        service.close()
    console.print(f"[green]✓ {escape(found.ref)} borrado.[/]")
    if found.provider == "gitlab":
        console.print("[dim]En GitLab puede quedar pendiente de borrado unos días según la configuración.[/]")


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------
@app.command()
@handle_errors
def tui() -> None:
    """Abre la interfaz interactiva."""
    from gitkeeper.tui import GitKeeperApp

    service = make_service(load_config())
    service.require_providers()
    try:
        GitKeeperApp(service).run()
    finally:
        service.close()


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
@auth_app.command("login")
@handle_errors
def auth_login(
    provider: Optional[str] = typer.Argument(None, help="github, gitlab o bitbucket."),
    token: Optional[str] = typer.Option(None, "--token", "-t", help="Token (si no, se pide sin mostrarlo)."),
    username: Optional[str] = typer.Option(None, "--username", "-u",
                                           help="Bitbucket: email de Atlassian o usuario."),
    url: Optional[str] = typer.Option(None, "--url", help="URL de la API (GitHub Enterprise, GitLab self-hosted)."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Bitbucket: workspace(s)."),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Comprueba el token antes de guardarlo."),
) -> None:
    """Guarda un token de acceso en la configuración."""
    if provider is None:
        provider = _choose(f"Plataforma ({'/'.join(PROVIDERS)})", list(PROVIDERS), "github")
    _check_provider(provider)
    config = load_config()

    if url:
        config.set(f"{provider}.api_url", url)
    if provider == "bitbucket":
        if workspace is not None:
            config.set("bitbucket.workspace", workspace)
        if username is None and token is None:
            username = typer.prompt(
                "Email de Atlassian (vacío si usas un access token de workspace/repositorio)",
                default=str(config.get("bitbucket", "username", "")), show_default=False,
            )
        if username is not None:
            config.set("bitbucket.username", username)

    if not token:
        console.print(f"[dim]{escape(TOKEN_HELP[provider])}[/]")
        token = typer.prompt("Token", hide_input=True).strip()
    if not token:
        raise GitKeeperError("El token no puede estar vacío.")

    if verify:
        prov = build_provider(provider, config, token, str(config.get(provider, "username", "")))
        try:
            with console.status(f"Comprobando credenciales de {LABELS[provider]}…"):
                user = prov.whoami()
        finally:
            prov.close()
        console.print(f"[green]✓ Autenticado en {LABELS[provider]} como [bold]{escape(user)}[/].[/]")

    config.set(f"{provider}.token", token)
    path = config.save()
    console.print(f"[dim]Guardado en {escape(str(path))}[/]")
    env_override = [name for name in TOKEN_ENV_VARS[provider] if os.environ.get(name, "").strip()]
    if env_override:
        console.print(f"[yellow]Aviso: la variable {env_override[0]} está definida y tiene prioridad.[/]")


@auth_app.command("status")
@handle_errors
def auth_status(
    offline: bool = typer.Option(False, "--offline", help="No contacta con las APIs."),
) -> None:
    """Muestra qué cuentas e IA están configuradas."""
    config = load_config()
    any_ok = False
    for name in PROVIDERS:
        creds = resolve_credentials(name, config)
        label = LABELS[name]
        if not creds:
            console.print(f"[dim]○ {label}: sin configurar[/]")
            continue
        if offline:
            console.print(f"[green]●[/] {label}: token desde {escape(creds.source)}")
            any_ok = True
            continue
        prov = build_provider(name, config, creds.token, creds.username)
        try:
            user = prov.whoami()
            console.print(f"[green]●[/] {label}: [bold]{escape(user)}[/] [dim](token desde {escape(creds.source)})[/]")
            any_ok = True
        except GitKeeperError as exc:
            console.print(f"[red]●[/] {label}: {escape(str(exc))} [dim](token desde {escape(creds.source)})[/]")
        finally:
            prov.close()

    ai_name = detect_provider(config)
    if ai_name:
        try:
            ai = get_ai_provider(config)
            console.print(f"[green]●[/] IA: {escape(ai.describe())}")
        except GitKeeperError as exc:
            console.print(f"[red]●[/] IA: {escape(str(exc))}")
    else:
        console.print("[dim]○ IA: sin configurar (define ANTHROPIC_API_KEY, GEMINI_API_KEY u OPENAI_API_KEY)[/]")
    if not any_ok:
        console.print("\nEjecuta [bold]gitkeeper auth login[/] para añadir una cuenta.")


@auth_app.command("logout")
@handle_errors
def auth_logout(provider: str = typer.Argument(..., help="github, gitlab o bitbucket.")) -> None:
    """Elimina el token guardado de una plataforma."""
    _check_provider(provider)
    config = load_config()
    config.unset(f"{provider}.token")
    if provider == "bitbucket":
        config.unset("bitbucket.username")
    config.save()
    console.print(f"[green]✓ Token de {LABELS[provider]} eliminado de la configuración.[/]")
    remaining = resolve_credentials(provider, config)
    if remaining:
        console.print(f"[yellow]Aviso: sigue habiendo credenciales desde {escape(remaining.source)}.[/]")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
@config_app.command("show")
@handle_errors
def config_show() -> None:
    """Muestra la configuración (con los secretos ocultos)."""
    config = load_config()
    console.print(f"[dim]{escape(str(config.path))}[/]")
    for section, values in config.redacted().items():
        console.print(f"[bold cyan]\\[{section}][/]")
        for key, value in values.items():
            shown = escape(str(value)) if value not in ("", None) else "[dim](vacío)[/]"
            console.print(f"  {key} = {shown}")


@config_app.command("set")
@handle_errors
def config_set(key: str = typer.Argument(..., help="seccion.clave, p. ej. ai.provider"),
               value: str = typer.Argument(..., help="Valor.")) -> None:
    """Cambia un valor de la configuración."""
    config = load_config()
    config.set(key, value)
    config.save()
    console.print(f"[green]✓ {escape(key)} actualizado.[/]")


@config_app.command("unset")
@handle_errors
def config_unset(key: str = typer.Argument(..., help="seccion.clave")) -> None:
    """Restablece un valor a su valor por defecto."""
    config = load_config()
    config.unset(key)
    config.save()
    console.print(f"[green]✓ {escape(key)} restablecido.[/]")


@config_app.command("path")
def config_path() -> None:
    """Muestra la ruta del fichero de configuración."""
    typer.echo(str(load_config().path))


def main() -> None:
    # En Windows la salida redirigida usa cp1252 y rompería con acentos y símbolos.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
