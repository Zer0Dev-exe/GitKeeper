"""Colores por lenguaje (los mismos que usa GitHub, de github-linguist)."""

from __future__ import annotations

import zlib

from rich.text import Text

LANGUAGE_COLORS: dict[str, str] = {
    "Python": "#3572A5",
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "Java": "#b07219",
    "Kotlin": "#A97BFF",
    "C": "#555555",
    "C++": "#f34b7d",
    "C#": "#178600",
    "Go": "#00ADD8",
    "Rust": "#dea584",
    "Ruby": "#701516",
    "PHP": "#4F5D95",
    "Swift": "#F05138",
    "Objective-C": "#438eff",
    "Dart": "#00B4AB",
    "Scala": "#c22d40",
    "Elixir": "#6e4a7e",
    "Erlang": "#B83998",
    "Haskell": "#5e5086",
    "Lua": "#000080",
    "Luau": "#00A2FF",
    "R": "#198CE7",
    "Julia": "#a270ba",
    "Perl": "#0298c3",
    "Shell": "#89e051",
    "PowerShell": "#012456",
    "Batchfile": "#C1F12E",
    "HTML": "#e34c26",
    "CSS": "#663399",
    "SCSS": "#c6538c",
    "Less": "#1d365d",
    "Vue": "#41b883",
    "Svelte": "#ff3e00",
    "Astro": "#ff5a03",
    "Jupyter Notebook": "#DA5B0B",
    "Dockerfile": "#384d54",
    "Makefile": "#427819",
    "CMake": "#DA3434",
    "Nix": "#7e7eff",
    "HCL": "#844FBA",
    "Zig": "#ec915c",
    "Nim": "#ffc200",
    "Clojure": "#db5855",
    "F#": "#b845fc",
    "OCaml": "#ef7a08",
    "Groovy": "#4298b8",
    "Assembly": "#6E4C13",
    "Solidity": "#AA6746",
    "GDScript": "#355570",
    "MDX": "#fcb32c",
    "Markdown": "#083fa1",
    "TeX": "#3D6117",
    "Vim Script": "#199f4b",
    "Emacs Lisp": "#c065db",
    "PLpgSQL": "#336790",
    "TSQL": "#e38c00",
    "Visual Basic .NET": "#945db7",
    "Pascal": "#E3F171",
    "Fortran": "#4d41b1",
    "COBOL": "#8a1267",
    "Prolog": "#74283c",
    "Twig": "#c1d026",
    "Blade": "#f7523f",
    "Handlebars": "#f7931e",
    "EJS": "#a91e50",
    "Pug": "#a86454",
}

# Para lenguajes sin color conocido: un color estable derivado del nombre.
_FALLBACK = ["#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#d19a66", "#be5046"]
# Colores muy oscuros que no se verían sobre fondo oscuro: se aclaran al pintarlos.
_TOO_DARK = {"#000080": "#4b6bff", "#012456": "#3a78c9", "#555555": "#8a8a8a", "#384d54": "#6d8c96",
             "#1d365d": "#4e79b8", "#6E4C13": "#a6793a", "#701516": "#c0392b"}


def language_color(language: str | None) -> str | None:
    if not language:
        return None
    color = LANGUAGE_COLORS.get(language)
    if color is None:
        for name, value in LANGUAGE_COLORS.items():
            if name.lower() == language.lower():
                color = value
                break
    if color is None:
        color = _FALLBACK[zlib.crc32(language.lower().encode()) % len(_FALLBACK)]
    return _TOO_DARK.get(color, color)


def language_badge(language: str | None, pending: bool = False) -> Text:
    """'● Python' con el punto y el nombre en el color del lenguaje.

    Sin lenguaje: 'Sin código' (p. ej. repos ``.github`` con solo Markdown/YAML, que GitHub
    no cuenta como código) o '…' si todavía no se ha consultado.
    """
    if not language:
        return Text("…", style="dim") if pending else Text("○ Sin código", style="dim")
    color = language_color(language)
    return Text.assemble(("● ", color or ""), (language, color or ""))
