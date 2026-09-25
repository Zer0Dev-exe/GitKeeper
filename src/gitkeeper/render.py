"""Salida de consola con Rich."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gitkeeper.colors import language_badge, language_color
from gitkeeper.models import Repo, Suggestion

PROVIDER_STYLE = {"github": "bold #F0F6FC", "gitlab": "bold #FC6D26", "bitbucket": "bold #2684FF"}
PROVIDER_SHORT = {"github": "GH", "gitlab": "GL", "bitbucket": "BB"}


def relative_time(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return "—"
    now = now or datetime.now(timezone.utc)
    seconds = int((now - value).total_seconds())
    if seconds < 0:
        return "ahora"
    if seconds < 60:
        return "hace un momento"
    units = [(365 * 86400, "año", "años"), (30 * 86400, "mes", "meses"), (7 * 86400, "semana", "semanas"),
             (86400, "día", "días"), (3600, "hora", "horas"), (60, "minuto", "minutos")]
    for size, singular, plural in units:
        if seconds >= size:
            n = seconds // size
            return f"hace {n} {singular if n == 1 else plural}"
    return "ahora"  # pragma: no cover


def one_line(text: str) -> str:
    """Aplana saltos de línea (GitLab permite descripciones multilínea)."""
    return " ".join(text.split())


def truncate(text: str, limit: int) -> str:
    text = one_line(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def languages_text(languages: dict[str, float] | None, fallback: str | None = None) -> Text:
    """Como format_languages, pero cada lenguaje con su color."""
    if not languages:
        return language_badge(fallback) if fallback else Text("No detectado", style="dim")
    text = Text()
    for i, (name, share) in enumerate(languages.items()):
        if i:
            text.append(" · ", style="dim")
        color = language_color(name) or ""
        text.append("● ", style=color)
        text.append(f"{name} {'<1' if share < 1 else round(share)}%", style=color)
    return text


def format_languages(languages: dict[str, float] | None, fallback: str | None = None) -> str:
    """'Python 80% · HTML 20%'. Sin datos, el lenguaje principal o 'No detectado'."""
    if not languages:
        return fallback or "No detectado"
    return " · ".join(f"{name} {'<1' if share < 1 else round(share)}%" for name, share in languages.items())


def format_topics(topics: list[str]) -> str:
    return ", ".join(topics) if topics else "No hay"


def provider_badge(provider: str) -> Text:
    return Text(PROVIDER_SHORT.get(provider, provider), style=PROVIDER_STYLE.get(provider, "bold"))


VISIBILITY_LABELS = {
    "public": ("Público", "#22C55E"),
    "private": ("Privado", "#F59E0B"),
    "internal": ("Interno", "#EAB308"),
}


def repo_flags(repo: Repo) -> Text:
    """Siempre la visibilidad, y además fork / archivado si aplica."""
    label, style = VISIBILITY_LABELS.get(repo.visibility, (repo.visibility, "yellow"))
    text = Text(label, style=style)
    if repo.fork:
        text.append(" · fork", style="#22D3EE")
    if repo.archived:
        text.append(" · archivado", style="#E879F9")
    return text


def repos_table(repos: list[Repo], title: str | None = None, date_field: str = "updated") -> Table:
    table = Table(title=title, header_style="bold", expand=False, pad_edge=False)
    table.add_column("", no_wrap=True)
    table.add_column("Repositorio", style="bold", overflow="fold")
    table.add_column("Lenguaje", no_wrap=True)
    table.add_column("Descripción", ratio=1, overflow="ellipsis", max_width=70)
    table.add_column("Estado", no_wrap=True)
    table.add_column("Creado" if date_field == "created" else "Actividad", style="dim", no_wrap=True)
    for repo in repos:
        when = repo.created_at if date_field == "created" else repo.updated_at
        description = (
            Text(one_line(repo.description)) if repo.description else Text("sin descripción", style="dim italic")
        )
        table.add_row(
            provider_badge(repo.provider),
            Text(repo.full_name, style="dim" if repo.archived else "bold"),
            language_badge(repo.language),
            description,
            repo_flags(repo),
            relative_time(when),
        )
    return table


def repo_panel(repo: Repo, languages: dict[str, float] | None = None) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()
    rows = [
        ("Proveedor", provider_badge(repo.provider)),
        ("Descripción", Text(repo.description or "—")),
        ("URL", Text(repo.url or "—", style="link " + repo.url if repo.url else "")),
        ("Visibilidad", Text(repo.visibility)),
        ("Archivado", Text("Sí" if repo.archived else "No", style="magenta" if repo.archived else "")),
        ("Fork", Text("Sí" if repo.fork else "No")),
        ("Lenguajes", languages_text(languages, repo.language)),
        ("Topics", Text(format_topics(repo.topics), style="" if repo.topics else "dim")),
        ("Rama", Text(repo.default_branch or "—")),
        ("Estrellas", Text(str(repo.stars))),
        ("Creado", Text(_date(repo.created_at))),
        ("Actividad", Text(f"{_date(repo.updated_at)} ({relative_time(repo.updated_at)})")),
    ]
    for label, value in rows:
        grid.add_row(label, value)
    return Panel(grid, title=escape(repo.ref), title_align="left", border_style="cyan")


def suggestion_panel(repo: Repo, suggestion: Suggestion, show_topics: bool = True) -> Panel:
    parts: list = [
        Text.assemble(("Actual:    ", "bold"), (repo.description or "(vacía)", "dim")),
        Text.assemble(("Propuesta: ", "bold green"), (suggestion.description, "green")),
    ]
    if show_topics and suggestion.topics:
        parts.append(Text.assemble(("Topics:    ", "bold"), (", ".join(suggestion.topics), "cyan")))
    if suggestion.warning:
        parts.append(Text("⚠ " + suggestion.warning, style="yellow"))
    return Panel(Group(*parts), title=escape(repo.ref), title_align="left", border_style="green")


def print_errors(console: Console, errors: dict[str, str]) -> None:
    for provider, message in errors.items():
        console.print(f"[yellow]⚠ {escape(provider)}:[/] {escape(message)}")


def _date(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else "—"
