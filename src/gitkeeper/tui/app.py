"""Interfaz interactiva (Textual)."""

from __future__ import annotations

import webbrowser

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.worker import get_current_worker
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Input, Static

from gitkeeper.errors import GitKeeperError
from gitkeeper.filters import Filters, all_groups
from gitkeeper.models import Repo, Suggestion
from gitkeeper.colors import language_badge
from gitkeeper.render import format_topics, languages_text, provider_badge, relative_time, repo_flags, truncate
from gitkeeper.service import SORT_KEYS, GitKeeper, filter_repos, sort_repos
from gitkeeper.tui.branding import THEME, logo
from gitkeeper.tui.screens import ConfirmScreen, EditScreen, FiltersScreen

DESCRIPTION_WIDTH = 45
SUGGEST_LABEL = "✨ Sugerir descripción con IA"
SORT_LABELS = {"updated": "actividad", "created": "creación", "name": "nombre", "stars": "estrellas"}


class GitKeeperApp(App[None]):
    TITLE = "GitKeeper"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("slash", "focus_search", "Buscar"),
        Binding("d", "describe", "Sugerir descripción (IA)"),
        Binding("e", "edit", "Editar"),
        Binding("a", "toggle_archive", "Archivar"),
        Binding("x,delete", "delete", "Borrar"),
        Binding("o", "open", "Abrir"),
        Binding("f", "filters", "Filtros"),
        Binding("s", "cycle_sort", "Orden"),
        Binding("r", "refresh", "Recargar"),
        Binding("escape", "clear_search", "Limpiar", show=False),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self, service: GitKeeper):
        super().__init__()
        self.register_theme(THEME)
        self.theme = THEME.name
        self.service = service
        self.repos: list[Repo] = []
        self.shown: list[Repo] = []
        self.filters = Filters.default()
        self.sort_key = SORT_KEYS[0]
        self.search_text = ""
        self.busy = False

    # ----------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        with Horizontal(id="brand"):
            yield Static(logo(), id="logo")
            yield Static(self.brand_info(), id="brand-info")
        with Horizontal(id="toolbar"):
            yield Input(placeholder="🔍 Buscar por nombre, descripción, lenguaje o topic…  ( / )", id="search")
            yield Button(self.filters_label(), id="filters", variant="primary", tooltip="Elegir filtros (tecla f)")
        yield Static("", id="status")
        with Horizontal(id="main"):
            yield DataTable(id="repos", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail-pane"):
                with Vertical(id="actions"):
                    yield Button(SUGGEST_LABEL, id="suggest", variant="success",
                                 tooltip="Genera una propuesta de descripción con IA (tecla d)")
                    yield Button("✎ Editar a mano", id="edit", variant="default", tooltip="Editar descripción y topics (tecla e)")
                yield Static("", id="detail")
        yield Footer()

    def brand_info(self) -> Text:
        """Cuentas conectadas e IA en uso, a la derecha de la barra de marca."""
        from gitkeeper.render import PROVIDER_STYLE

        text = Text()
        for name, provider in self.service.providers.items():
            text.append("● ", style=PROVIDER_STYLE.get(name, "bold"))
            text.append(f"{provider.label}  ", style="#D1D5DB")
        try:
            ai = self.service.ai.describe()
        except GitKeeperError:
            ai = "sin configurar"
        text.append("✨ IA: ", style="bold #A855F7")
        text.append(ai, style="#D1D5DB")
        return text

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "Repositorio", "Lenguaje", "Descripción", "Estado", "Actividad")
        table.focus()
        self.load(refresh=False)

    # ------------------------------------------------------------------ datos
    @work(thread=True, exclusive=True, group="load")
    def load(self, refresh: bool = True) -> None:
        self.call_from_thread(self.set_status, "Cargando repositorios…")
        try:
            result = self.service.fetch_all(refresh=refresh)
        except GitKeeperError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error", timeout=8)
            self.call_from_thread(self.set_status, "Error al cargar")
            return
        for provider, message in result.errors.items():
            self.call_from_thread(self.notify, f"{provider}: {message}", severity="warning", timeout=8)
        self.call_from_thread(self.set_repos, list(result.repos))

    def set_repos(self, repos: list[Repo]) -> None:
        self.repos = repos
        self.refresh_table()

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def filtered(self) -> list[Repo]:
        repos = self.filters.apply(self.repos, self.main_language)
        if self.search_text:
            repos = filter_repos(repos, query=self.search_text)
        return sort_repos(repos, self.sort_key)

    def filter_groups(self):
        return all_groups(self.repos, self.main_language)

    def filters_label(self) -> str:
        count = self.filters.count()
        return f"⚙ Filtros ({count})" if count else "⚙ Filtros"

    def refresh_table(self, keep: Repo | None = None) -> None:
        table = self.query_one(DataTable)
        keep = keep or self.selected
        self.shown = self.filtered()
        table.clear()
        for repo in self.shown:
            date = repo.created_at if self.sort_key == "created" else repo.updated_at
            table.add_row(
                provider_badge(repo.provider),
                Text(repo.full_name, style="dim" if repo.archived else "bold"),
                language_badge(self.main_language(repo), pending=self.language_pending(repo)),
                Text(truncate(repo.description, DESCRIPTION_WIDTH))
                if repo.description else Text("sin descripción", style="dim italic"),
                repo_flags(repo),
                relative_time(date),
                key=repo.ref,
            )
        if keep:
            for index, repo in enumerate(self.shown):
                if repo.ref == keep.ref:
                    table.move_cursor(row=index)
                    break
        self.set_status(
            f"{len(self.shown)}/{len(self.repos)} · {self.filters.summary(self.filter_groups())} · "
            f"orden: {SORT_LABELS[self.sort_key]}"
        )
        self.update_detail()

    def main_language(self, repo: Repo) -> str | None:
        """Lenguaje principal; en GitLab (que no lo da al listar) sale del detalle ya cargado."""
        if repo.language:
            return repo.language
        cached = self.service.cached_languages(repo)
        return next(iter(cached), None) if cached else None

    def language_pending(self, repo: Repo) -> bool:
        """GitLab no da el lenguaje al listar: hasta cargarlo no se sabe si hay código."""
        return (
            not repo.language
            and self.service.cached_languages(repo) is None
            and not self.service.provider_for(repo).lists_language
        )

    @property
    def selected(self) -> Repo | None:
        table = self.query_one(DataTable)
        if not self.shown or table.row_count == 0:
            return None
        row = table.cursor_row
        return self.shown[row] if 0 <= row < len(self.shown) else None

    def update_detail(self) -> None:
        repo = self.selected
        detail = self.query_one("#detail", Static)
        self.query_one("#actions").display = repo is not None
        if repo is None:
            detail.update("[dim]Sin selección[/]")
            return
        cached = self.service.cached_languages(repo)
        if cached is None:
            languages = "[dim]cargando…[/]"
            self.load_languages(repo)
        else:
            languages = languages_text(cached, repo.language).markup
        lines = [
            f"[b $primary]{escape(repo.full_name)}[/]",
            f"{provider_badge(repo.provider).markup} {repo_flags(repo).markup}",
            "",
            escape(repo.description) if repo.description else "[dim italic]sin descripción[/]",
            "",
            f"[b]Archivado:[/] {'[magenta]Sí[/]' if repo.archived else 'No'}",
            f"[b]Lenguajes:[/] {languages}",
            f"[b]Topics:[/] {escape(format_topics(repo.topics)) if repo.topics else '[dim]No hay[/]'}",
            f"[b]Estrellas:[/] {repo.stars}",
            f"[b]Rama:[/] {escape(repo.default_branch or '—')}",
            f"[b]Creado:[/] {relative_time(repo.created_at)}",
            f"[b]Actividad:[/] {relative_time(repo.updated_at)}",
            "",
            f"[dim]{escape(repo.url)}[/]",
        ]
        detail.update("\n".join(lines))

    @work(thread=True, exclusive=True, group="languages")
    def load_languages(self, repo: Repo) -> None:
        self.service.languages(repo)
        if get_current_worker().is_cancelled:
            return
        self.call_from_thread(self._languages_loaded, repo)

    def _languages_loaded(self, repo: Repo) -> None:
        if not repo.language and self.service.cached_languages(repo):
            # GitLab: ahora ya se conoce el lenguaje principal para la columna.
            self.refresh_table(keep=repo)
            return
        selected = self.selected
        if selected is not None and selected.ref == repo.ref:
            self.update_detail()

    def replace_repo(self, updated: Repo) -> None:
        for i, repo in enumerate(self.repos):
            if repo.provider == updated.provider and repo.id == updated.id:
                self.repos[i] = updated
                break
        self.refresh_table(keep=updated)

    def remove_repo(self, removed: Repo) -> None:
        self.repos = [r for r in self.repos if not (r.provider == removed.provider and r.id == removed.id)]
        self.refresh_table()

    # ---------------------------------------------------------------- eventos
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.update_detail()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.search_text = event.value.strip()
            self.refresh_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self.query_one(DataTable).focus()

    # --------------------------------------------------------------- acciones
    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""
        self.query_one(DataTable).focus()

    def action_filters(self) -> None:
        def done(result: Filters | None) -> None:
            if result is not None:
                self.set_filters(result)

        self.push_screen(FiltersScreen(self.filter_groups(), self.filters), done)

    def set_filters(self, filters: Filters) -> None:
        self.filters = filters
        self.query_one("#filters", Button).label = self.filters_label()
        self.refresh_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "filters":
            self.action_filters()
            return
        if event.button.id == "suggest":
            self.action_describe()
        elif event.button.id == "edit":
            self.action_edit()

    def action_cycle_sort(self) -> None:
        self.sort_key = SORT_KEYS[(SORT_KEYS.index(self.sort_key) + 1) % len(SORT_KEYS)]
        self.refresh_table()

    def action_refresh(self) -> None:
        self.load(refresh=True)

    def action_open(self) -> None:
        repo = self.selected
        if repo and repo.url:
            webbrowser.open(repo.url)

    def action_edit(self) -> None:
        repo = self.selected
        if repo is None:
            return
        provider = self.service.provider_for(repo)
        screen = EditScreen(
            f"Editar {repo.ref}", repo.description, Suggestion(repo.description, list(repo.topics)),
            with_topics=provider.supports_topics,
        )

        def done(result: Suggestion | None) -> None:
            if result is not None:
                topics = result.topics if provider.supports_topics and result.topics != repo.topics else None
                self.save_description(repo, result.description, topics)

        self.push_screen(screen, done)

    def action_describe(self) -> None:
        repo = self.selected
        if repo is None or self.busy:
            return
        self.set_busy(True)
        self.generate(repo)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        button = self.query_one("#suggest", Button)
        button.disabled = busy
        button.label = "⏳ Generando…" if busy else SUGGEST_LABEL

    @work(thread=True, exclusive=True, group="ai")
    def generate(self, repo: Repo) -> None:
        self.call_from_thread(self.set_status, f"Generando descripción de {repo.full_name} con IA…")
        try:
            suggestion = self.service.suggest(repo)
        except GitKeeperError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error", timeout=8)
            return
        finally:
            self.call_from_thread(self.set_busy, False)
            self.call_from_thread(self.refresh_table)
        self.call_from_thread(self.review, repo, suggestion)

    def review(self, repo: Repo, suggestion: Suggestion) -> None:
        provider = self.service.provider_for(repo)
        screen = EditScreen(
            f"Propuesta de la IA para {repo.ref}", repo.description, suggestion,
            with_topics=provider.supports_topics, save_label="Aplicar",
        )

        def done(result: Suggestion | None) -> None:
            if result is not None:
                self.save_description(repo, result.description, result.topics if provider.supports_topics else None)

        self.push_screen(screen, done)

    @work(thread=True, group="write")
    def save_description(self, repo: Repo, description: str, topics: list[str] | None) -> None:
        try:
            updated = self.service.update_description(repo, description, topics)
        except GitKeeperError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error", timeout=8)
            return
        self.call_from_thread(self.replace_repo, updated)
        self.call_from_thread(self.notify, f"{repo.ref} actualizado")

    def action_toggle_archive(self) -> None:
        repo = self.selected
        if repo is None:
            return
        provider = self.service.provider_for(repo)
        if not provider.supports_archive:
            self.notify(f"{provider.label} no permite archivar por API.", severity="warning")
            return
        verb = "Desarchivar" if repo.archived else "Archivar"

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.archive(repo, not repo.archived)

        self.push_screen(ConfirmScreen(f"¿{verb} [b]{escape(repo.ref)}[/]?", verb), done)

    @work(thread=True, group="write")
    def archive(self, repo: Repo, archived: bool) -> None:
        try:
            updated = self.service.set_archived(repo, archived)
        except GitKeeperError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error", timeout=8)
            return
        self.call_from_thread(self.replace_repo, updated)
        self.call_from_thread(self.notify, f"{repo.ref} {'archivado' if archived else 'desarchivado'}")

    def action_delete(self) -> None:
        repo = self.selected
        if repo is None:
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.delete(repo)

        message = f"[b red]¿Borrar {escape(repo.ref)}?[/]\nEsta acción es irreversible."
        self.push_screen(ConfirmScreen(message, "CONFIRMAR", danger=True), done)

    @work(thread=True, group="write")
    def delete(self, repo: Repo) -> None:
        try:
            self.service.delete(repo)
        except GitKeeperError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error", timeout=8)
            return
        self.call_from_thread(self.remove_repo, repo)
        self.call_from_thread(self.notify, f"{repo.ref} borrado")
