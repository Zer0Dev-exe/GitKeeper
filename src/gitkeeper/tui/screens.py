"""Ventanas modales de la TUI."""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, SelectionList, Static

from gitkeeper.filters import FilterGroup, Filters
from gitkeeper.models import Suggestion


class ConfirmScreen(ModalScreen[bool]):
    """Pregunta sí/no."""

    BINDINGS = [Binding("escape", "cancel", "Cancelar"), Binding("y", "confirm", "Sí", show=False)]

    def __init__(self, message: str, confirm_label: str = "Sí", danger: bool = False):
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.message, classes="dialog-message")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self.confirm_label, id="confirm", variant="error" if self.danger else "primary")
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel" if self.danger else "#confirm", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class EditScreen(ModalScreen[Suggestion | None]):
    """Edita (o revisa la propuesta de la IA para) la descripción y los topics."""

    BINDINGS = [Binding("escape", "cancel", "Cancelar"), Binding("ctrl+s", "save", "Guardar")]

    def __init__(self, title: str, current: str, proposal: Suggestion, with_topics: bool, save_label: str = "Guardar"):
        super().__init__()
        self.title_text = title
        self.current = current
        self.proposal = proposal
        self.with_topics = with_topics
        self.save_label = save_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Static(f"[b]{escape(self.title_text)}[/]", classes="dialog-message")
            yield Label(f"[dim]Actual:[/] {escape(self.current or '(vacía)')}")
            if self.proposal.warning:
                yield Label(f"[yellow]⚠ {escape(self.proposal.warning)}[/]", classes="warning")
            yield Label("Descripción:")
            yield Input(value=self.proposal.description, id="description")
            if self.with_topics:
                yield Label("Topics (separados por comas):")
                yield Input(value=", ".join(self.proposal.topics), id="topics")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self.save_label, id="save", variant="success")
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#description", Input).focus()

    def _result(self) -> Suggestion:
        description = self.query_one("#description", Input).value.strip()
        topics: list[str] = []
        if self.with_topics:
            raw = self.query_one("#topics", Input).value
            topics = [t.strip() for t in raw.split(",") if t.strip()]
        return Suggestion(description, topics)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self.dismiss(self._result())

    def action_cancel(self) -> None:
        self.dismiss(None)


class FiltersScreen(ModalScreen["Filters | None"]):
    """Ventana de filtros: varias opciones por grupo (O dentro del grupo, Y entre grupos)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
        Binding("ctrl+s", "apply", "Aplicar"),
        Binding("ctrl+l", "clear", "Limpiar"),
    ]

    def __init__(self, groups: list[FilterGroup], current: Filters):
        super().__init__()
        self.groups = groups
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog filters"):
            yield Static("[b]Filtros[/]  [dim]Marca con espacio o clic. Dentro de un grupo vale cualquiera; "
                         "entre grupos, todos a la vez.[/]", classes="dialog-message")
            with VerticalScroll(id="filter-groups"):
                with Grid(id="filter-grid"):
                    for group in self.groups:
                        chosen = self.current.selected.get(group.key, set())
                        with Vertical(classes="filter-group"):
                            yield Label(f"[b]{escape(group.title)}[/]")
                            yield SelectionList[str](
                                *[(escape(label), value, value in chosen) for value, label in group.options],
                                id=f"group-{group.key}",
                                compact=True,
                            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Aplicar", id="apply", variant="success")
                yield Button("Limpiar", id="clear", variant="warning")
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        lists = self.query(SelectionList)
        if lists:
            lists.first().focus()

    def result(self) -> Filters:
        selected: dict[str, set[str]] = {}
        for group in self.groups:
            values = set(self.query_one(f"#group-{group.key}", SelectionList).selected)
            if values:
                selected[group.key] = values
        return Filters(selected)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self.action_apply()
        elif event.button.id == "clear":
            self.action_clear()
        else:
            self.dismiss(None)

    def action_apply(self) -> None:
        self.dismiss(self.result())

    def action_clear(self) -> None:
        for selection_list in self.query(SelectionList):
            selection_list.deselect_all()

    def action_cancel(self) -> None:
        self.dismiss(None)
