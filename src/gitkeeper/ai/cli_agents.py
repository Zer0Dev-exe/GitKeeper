"""IA a través de las CLIs oficiales, usando la suscripción del usuario en lugar de una API key.

- Claude Code (``claude``): suscripción Claude Pro/Max.
- Codex CLI (``codex``): suscripción ChatGPT Plus/Pro.
- Gemini CLI (``gemini``): cuenta de Google (plan gratuito o Gemini Advanced / Code Assist).

Cada CLI debe estar instalada y con la sesión iniciada (``claude`` → /login, ``codex login``,
``gemini`` → Login with Google). Las variables de API key se eliminan del entorno del
proceso hijo para que la CLI use la suscripción y no facture por API.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from gitkeeper.ai.base import AIProvider
from gitkeeper.ai.prompt import SCHEMA
from gitkeeper.errors import AIError

Runner = Callable[..., subprocess.CompletedProcess]
TIMEOUT = 300


def _vscode_claude_binaries() -> list[Path]:
    """Binario de Claude Code incluido en la extensión de VS Code (la versión más reciente primero)."""
    exe = "claude.exe" if os.name == "nt" else "claude"
    found: list[Path] = []
    for editor_dir in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        base = Path.home() / editor_dir / "extensions"
        if base.is_dir():
            found.extend(base.glob(f"anthropic.claude-code-*/resources/native-binary/{exe}"))
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


class CLIProvider(AIProvider):
    binary: str = ""
    install_hint: str = ""
    login_hint: str = ""
    # Variables que harían que la CLI use una API key en vez de la suscripción.
    strip_env: tuple[str, ...] = ()

    def __init__(self, model: str = "", path: str = "", runner: Runner | None = None, timeout: int = TIMEOUT):
        super().__init__(model=model)
        self.path = path
        self.runner = runner or subprocess.run
        self.timeout = timeout

    def extra_locations(self) -> list[Path]:
        return []

    def find_binary(self) -> str | None:
        if self.path:
            return self.path if Path(self.path).exists() or shutil.which(self.path) else None
        found = shutil.which(self.binary)
        if found:
            return found
        for candidate in self.extra_locations():
            if candidate.exists():
                return str(candidate)
        return None

    def is_available(self) -> bool:
        return self.find_binary() is not None

    def describe(self) -> str:
        model = f", {self.model}" if self.model else ""
        return f"{self.label} (suscripción{model})"

    def run(self, args: list[str], stdin: str) -> subprocess.CompletedProcess:
        binary = self.find_binary()
        if binary is None:
            raise AIError(f"No se encontró '{self.binary}'. {self.install_hint}")
        env = {k: v for k, v in os.environ.items() if k not in self.strip_env}
        try:
            result = self.runner(
                [binary, *args],
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=env,
                # Directorio neutro: que la CLI no cargue instrucciones del proyecto actual.
                cwd=tempfile.gettempdir(),
            )
        except FileNotFoundError as exc:
            raise AIError(f"No se pudo ejecutar '{binary}'. {self.install_hint}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AIError(f"{self.label} no respondió en {self.timeout} s.") from exc
        return result

    def fail(self, result: subprocess.CompletedProcess, detail: str = "") -> AIError:
        text = (detail or result.stderr or result.stdout or "").strip()
        lowered = text.lower()
        hint = ""
        if any(word in lowered for word in ("login", "log in", "logged in", "auth", "credential", "unauthorized")):
            hint = f" {self.login_hint}"
        return AIError(f"{self.label}: {text[-500:] or f'código de salida {result.returncode}'}.{hint}")


class ClaudeCodeProvider(CLIProvider):
    name = "claude-code"
    label = "Claude Code"
    binary = "claude"
    install_hint = "Instala Claude Code (https://claude.com/claude-code) o indica la ruta con ai.cli_path."
    login_hint = "Inicia sesión ejecutando 'claude' y luego /login con tu cuenta Pro/Max."
    strip_env = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

    def extra_locations(self) -> list[Path]:
        home = Path.home()
        exe = "claude.exe" if os.name == "nt" else "claude"
        return [home / ".local" / "bin" / exe, home / ".claude" / "local" / exe, *_vscode_claude_binaries()]

    def complete(self, system: str, user: str) -> str:
        args = [
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA),
            "--system-prompt", system,
            "--tools", "",
            "--no-session-persistence",
            "--strict-mcp-config",
        ]
        if self.model:
            args += ["--model", self.model]
        result = self.run(args, user)
        try:
            data: Any = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise self.fail(result) from None
        if not isinstance(data, dict):
            raise self.fail(result)
        if data.get("is_error") or result.returncode != 0:
            raise self.fail(result, str(data.get("result") or data.get("subtype") or ""))
        structured = data.get("structured_output")
        if isinstance(structured, dict):
            return json.dumps(structured)
        text = data.get("result")
        if not isinstance(text, str) or not text.strip():
            raise AIError("Claude Code no devolvió ninguna respuesta.")
        return text


class CodexProvider(CLIProvider):
    name = "codex"
    label = "Codex (ChatGPT)"
    binary = "codex"
    install_hint = "Instala Codex CLI (npm install -g @openai/codex) o indica la ruta con ai.cli_path."
    login_hint = "Inicia sesión con 'codex login' usando tu cuenta de ChatGPT."
    strip_env = ("OPENAI_API_KEY",)

    def complete(self, system: str, user: str) -> str:
        # Codex no tiene opción de system prompt: se antepone a la petición.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "last-message.txt"
            args = ["exec", "--skip-git-repo-check", "--output-last-message", str(out)]
            if self.model:
                args += ["--model", self.model]
            args.append("-")  # lee el prompt de stdin
            result = self.run(args, f"{system}\n\n{user}")
            if result.returncode != 0:
                raise self.fail(result)
            text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
        text = text.strip() or result.stdout.strip()
        if not text:
            raise AIError("Codex no devolvió ninguna respuesta.")
        return text


class GeminiCLIProvider(CLIProvider):
    name = "gemini-cli"
    label = "Gemini CLI"
    binary = "gemini"
    install_hint = "Instala Gemini CLI (npm install -g @google/gemini-cli) o indica la ruta con ai.cli_path."
    login_hint = "Ejecuta 'gemini' y elige 'Login with Google'."
    strip_env = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

    def complete(self, system: str, user: str) -> str:
        args = ["--output-format", "json"]
        if self.model:
            args += ["--model", self.model]
        result = self.run(args, f"{system}\n\n{user}")
        if result.returncode != 0:
            raise self.fail(result)
        stdout = result.stdout.strip()
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            if data.get("error"):
                error = data["error"]
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise self.fail(result, str(message))
            response = data.get("response")
            if isinstance(response, str) and response.strip():
                return response
            if "description" in data:
                return stdout  # la propia respuesta ya es el JSON pedido
        if not stdout:
            raise AIError("Gemini CLI no devolvió ninguna respuesta.")
        return stdout


CLI_PROVIDERS: dict[str, type[CLIProvider]] = {
    "claude-code": ClaudeCodeProvider,
    "codex": CodexProvider,
    "gemini-cli": GeminiCLIProvider,
}
