import pytest
from conftest import FakeAI, FakeProvider, make_repo
from textual.widgets import DataTable, Input, Static

from gitkeeper.errors import ProviderError
from gitkeeper.service import GitKeeper
from gitkeeper.tui import GitKeeperApp
from gitkeeper.tui.screens import ConfirmScreen, EditScreen

SIZE = (160, 40)


def build(ai=None, **kwargs):
    gh = FakeProvider("github", [
        make_repo("app", description="Main app", language="Python"),
        make_repo("empty"),
        make_repo("old", archived=True),
    ], **kwargs)
    ai = ai or FakeAI()
    service = GitKeeper([gh], ai_factory=lambda: ai)
    return GitKeeperApp(service), gh, ai


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def names(app):
    return [r.name for r in app.shown]


async def select(app, pilot, name):
    table = app.query_one(DataTable)
    table.move_cursor(row=names(app).index(name))
    await pilot.pause()


async def test_loads_active_repos_by_default():
    app, gh, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert sorted(names(app)) == ["app", "empty"]
        assert app.query_one(DataTable).row_count == 2
        status = str(app.query_one("#status", Static).render())
        assert "2/3" in status and "Activos" in status
        assert str(app.query_one("#filters").label) == "⚙ Filtros (1)"


async def test_detail_follows_selection():
    app, _, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await select(app, pilot, "app")
        assert "Main app" in str(app.query_one("#detail", Static).render())


async def test_search_filters_and_letters_do_not_trigger_actions():
    app, gh, ai = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("slash")
        assert isinstance(app.focused, Input)
        await pilot.press("m", "a", "i", "n", "d")  # "d" no debe lanzar la IA
        await pilot.pause()
        assert app.query_one("#search", Input).value == "maind"
        assert ai.prompts == []
        await pilot.press("backspace")
        await pilot.pause()
        assert names(app) == ["app"]
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one("#search", Input).value == ""
        assert isinstance(app.focused, DataTable)
        assert len(names(app)) == 2


async def test_set_filters_and_sort():
    from gitkeeper.filters import Filters

    app, _, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        app.set_filters(Filters())  # sin filtros: todos
        assert len(names(app)) == 3
        assert str(app.query_one("#filters").label) == "⚙ Filtros"
        app.set_filters(Filters({"status": {"archived"}}))
        assert names(app) == ["old"]
        app.set_filters(Filters({"status": {"active"}, "description": {"without"}}))
        assert names(app) == ["empty"]
        app.set_filters(Filters.default())
        assert len(names(app)) == 2
        await pilot.press("s", "s")  # orden por nombre
        assert app.sort_key == "name"
        assert names(app) == ["app", "empty"]


async def test_describe_with_ai_and_apply():
    app, gh, ai = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await select(app, pilot, "empty")
        await pilot.press("d")
        await settle(app, pilot)
        assert isinstance(app.screen, EditScreen)
        assert app.screen.query_one("#description", Input).value == "Generated description"
        assert app.screen.query_one("#topics", Input).value == "cli, python"
        await pilot.click("#save")
        await settle(app, pilot)
        assert ("description", "alice/empty", "Generated description") in gh.calls
        assert ("topics", "alice/empty", ["cli", "python"]) in gh.calls
        repo = next(r for r in app.repos if r.name == "empty")
        assert repo.description == "Generated description"
        assert app.selected.name == "empty"


async def test_describe_cancel_does_nothing():
    app, gh, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("d")
        await settle(app, pilot)
        await pilot.press("escape")
        await settle(app, pilot)
        assert not any(c[0] == "description" for c in gh.calls)


async def test_describe_ai_error_notifies():
    app, gh, _ = build(ai=FakeAI(["garbage"]))
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("d")
        await settle(app, pilot)
        assert not isinstance(app.screen, EditScreen)
        assert any("JSON" in n.message for n in app._notifications)


async def test_manual_edit():
    app, gh, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await select(app, pilot, "app")
        await pilot.press("e")
        await pilot.pause()
        field = app.screen.query_one("#description", Input)
        field.value = "Edited"
        await pilot.press("enter")
        await settle(app, pilot)
        assert ("description", "alice/app", "Edited") in gh.calls
        # Topics sin cambios: no se reenvían.
        assert not any(c[0] == "topics" for c in gh.calls)


async def test_archive_toggle_with_confirmation():
    app, gh, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await select(app, pilot, "app")
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")
        await settle(app, pilot)
        assert not any(c[0] == "archive" for c in gh.calls)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert ("archive", "alice/app", True) in gh.calls
        assert "app" not in names(app)  # la vista "activos" ya no lo muestra


async def test_archive_unsupported_warns():
    app, gh, _ = build(supports_archive=False)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        assert any("no permite archivar" in n.message for n in app._notifications)


async def test_delete_simple_confirm():
    app, gh, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await select(app, pilot, "app")
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "CONFIRMAR" in str(app.screen.query_one("#confirm").label)
        # Por seguridad el foco empieza en Cancelar: Enter no borra.
        await pilot.press("enter")
        await settle(app, pilot)
        assert "alice/app" in gh.repos
        await pilot.press("x")
        await pilot.pause()
        await pilot.click("#confirm")
        await settle(app, pilot)
        assert ("delete", "alice/app") in gh.calls
        assert "app" not in names(app)


async def test_refresh_and_load_errors():
    app, gh, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        gh.fail = ProviderError("GitHub: caído")
        await pilot.press("r")
        await settle(app, pilot)
        assert any("caído" in n.message for n in app._notifications)


async def test_open_in_browser(monkeypatch):
    opened = []
    monkeypatch.setattr("gitkeeper.tui.app.webbrowser.open", opened.append)
    app, _, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await select(app, pilot, "app")
        await pilot.press("o")
        assert opened == ["https://github.example/alice/app"]


@pytest.mark.parametrize("key", ["d", "e", "a", "x", "o"])
async def test_actions_with_empty_list_are_safe(key):
    service = GitKeeper([FakeProvider("github", [])], ai_factory=lambda: FakeAI())
    app = GitKeeperApp(service)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)
        assert app.selected is None
        assert "Sin selección" in str(app.query_one("#detail", Static).render())


async def test_filters_screen_multi_select():
    from textual.widgets import SelectionList

    from gitkeeper.tui.screens import FiltersScreen

    app, _, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.click("#filters")
        await pilot.pause()
        assert isinstance(app.screen, FiltersScreen)
        status = app.screen.query_one("#group-status", SelectionList)
        assert status.selected == ["active"]  # refleja los filtros actuales
        app.screen.query_one("#group-description", SelectionList).select("without")
        await pilot.click("#apply")
        await pilot.pause()
        assert names(app) == ["empty"]
        assert str(app.query_one("#filters").label) == "⚙ Filtros (2)"
        assert "Sin descripción" in str(app.query_one("#status", Static).render())


async def test_filters_screen_clear_and_cancel():
    from gitkeeper.tui.screens import FiltersScreen

    app, _, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        await pilot.press("escape")  # cancelar: no cambia nada
        await pilot.pause()
        assert len(names(app)) == 2
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, FiltersScreen)
        await pilot.click("#clear")
        await pilot.click("#apply")
        await pilot.pause()
        assert len(names(app)) == 3  # sin filtros se ven también los archivados
        assert "sin filtros" in str(app.query_one("#status", Static).render())


async def test_suggest_button_opens_ai_review():
    app, gh, ai = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await select(app, pilot, "empty")
        await pilot.click("#suggest")
        await settle(app, pilot)
        assert isinstance(app.screen, EditScreen)
        assert len(ai.prompts) == 1
        await pilot.click("#save")
        await settle(app, pilot)
        assert ("description", "alice/empty", "Generated description") in gh.calls
        button = app.query_one("#suggest")
        assert not button.disabled and "Sugerir" in str(button.label)


async def test_edit_button():
    app, _, _ = build()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.click("#edit")
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)


async def test_buttons_hidden_without_selection():
    service = GitKeeper([FakeProvider("github", [])], ai_factory=lambda: FakeAI())
    app = GitKeeperApp(service)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert app.query_one("#actions").display is False


async def test_suggest_button_label_and_warning_shown():
    from test_service import NoContentProvider
    from textual.widgets import Label

    service = GitKeeper([NoContentProvider("github", [make_repo("app")])], ai_factory=lambda: FakeAI())
    app = GitKeeperApp(service)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert "Sugerir descripción con IA" in str(app.query_one("#suggest").label)
        await pilot.click("#suggest")
        await settle(app, pilot)
        assert isinstance(app.screen, EditScreen)
        warnings = [str(w.render()) for w in app.screen.query(Label) if w.has_class("warning")]
        assert warnings and "Sin permiso" in warnings[0]
