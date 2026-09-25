from conftest import FakeProvider, make_repo
from textual.widgets import DataTable

from gitkeeper.colors import language_badge, language_color
from gitkeeper.render import languages_text, truncate
from gitkeeper.service import GitKeeper


def test_known_language_colors():
    assert language_color("Python") == "#3572A5"
    assert language_color("typescript") == "#3178c6"  # sin distinguir mayúsculas
    assert language_color(None) is None


def test_dark_colors_are_lightened():
    # Lua es azul marino (#000080): ilegible sobre fondo oscuro.
    assert language_color("Lua") != "#000080"


def test_unknown_language_gets_stable_color():
    first = language_color("MiLenguajeRaro")
    assert first and first.startswith("#")
    assert language_color("MiLenguajeRaro") == first


def test_language_badge():
    badge = language_badge("Python")
    assert badge.plain == "● Python"
    assert any("#3572A5" in str(span.style) for span in badge.spans)
    assert language_badge(None).plain == "○ Sin código"
    assert language_badge(None, pending=True).plain == "…"


def test_languages_text_colored():
    text = languages_text({"Python": 75.0, "HTML": 25.0})
    assert text.plain == "● Python 75% · ● HTML 25%"
    styles = " ".join(str(span.style) for span in text.spans)
    assert "#3572A5" in styles and "#e34c26" in styles
    assert languages_text({}, "Go").plain == "● Go"
    assert languages_text(None).plain == "No detectado"


def test_truncate():
    assert truncate("corto", 10) == "corto"
    assert truncate("una descripción bastante larga", 10) == "una descr…"
    assert truncate("a\nb", 10) == "a b"


async def test_tui_table_has_colored_language_column():
    from gitkeeper.tui import GitKeeperApp

    long = "x" * 200
    provider = FakeProvider("github", [make_repo("app", language="Python", description=long)])
    app = GitKeeperApp(GitKeeper([provider]))
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one(DataTable)
        labels = [str(col.label) for col in table.columns.values()]
        assert labels == ["", "Repositorio", "Lenguaje", "Descripción", "Estado", "Actividad"]
        row = table.get_row_at(0)
        assert row[2].plain == "● Python"
        assert len(row[3].plain) <= 60  # descripción recortada: no empuja las demás columnas


async def test_gitlab_language_column_filled_after_loading():
    from gitkeeper.tui import GitKeeperApp

    class GitLabLike(FakeProvider):
        def get_languages(self, repo):
            return {"Ruby": 90.0, "Vue": 10.0}

    provider = GitLabLike("gitlab", [make_repo("app", provider="gitlab")])
    app = GitKeeperApp(GitKeeper([provider]))
    async with app.run_test(size=(160, 30)) as pilot:
        for _ in range(3):
            await pilot.pause()
            await app.workers.wait_for_complete()
        assert app.query_one(DataTable).get_row_at(0)[2].plain == "● Ruby"
