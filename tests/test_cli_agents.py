import json
import subprocess
from pathlib import Path

import pytest
from conftest import make_repo

from gitkeeper.ai import (
    ClaudeCodeProvider,
    CodexProvider,
    GeminiCLIProvider,
    detect_provider,
    get_ai_provider,
)
from gitkeeper.ai import cli_agents
from gitkeeper.ai.prompt import SCHEMA
from gitkeeper.errors import AIError

GOOD = {"description": "Gestor de repos", "topics": ["cli"]}


class FakeRunner:
    """Sustituye a subprocess.run y registra la llamada."""

    def __init__(self, stdout="", stderr="", returncode=0, write_file=None, exc=None):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.write_file = write_file
        self.exc = exc
        self.args = None
        self.kwargs = None

    def __call__(self, args, **kwargs):
        self.args, self.kwargs = args, kwargs
        if self.exc:
            raise self.exc
        if self.write_file is not None and "--output-last-message" in args:
            Path(args[args.index("--output-last-message") + 1]).write_text(self.write_file, encoding="utf-8")
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def provider(cls, runner, **kwargs):
    return cls(path=__file__, runner=runner, **kwargs)  # cualquier fichero existente sirve de "binario"


def claude_json(**extra):
    data = {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(GOOD),
            "structured_output": GOOD}
    data.update(extra)
    return json.dumps(data)


# ------------------------------------------------------------ Claude Code
def test_claude_code_command_and_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("KEEP_ME", "1")
    runner = FakeRunner(stdout=claude_json())
    result = provider(ClaudeCodeProvider, runner, model="sonnet").suggest(make_repo(), "readme", [], "es")
    assert result.description == "Gestor de repos"
    args = runner.args
    assert args[0] == __file__
    assert args[1:4] == ["-p", "--output-format", "json"]
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--model") + 1] == "sonnet"
    assert "--system-prompt" in args and "--no-session-persistence" in args
    # El prompt va por stdin, no en la línea de comandos.
    assert "readme" in runner.kwargs["input"]
    env = runner.kwargs["env"]
    assert "ANTHROPIC_API_KEY" not in env and env["KEEP_ME"] == "1"


def test_claude_code_without_model_flag():
    runner = FakeRunner(stdout=claude_json())
    provider(ClaudeCodeProvider, runner).complete("s", "u")
    assert "--model" not in runner.args


def test_claude_code_falls_back_to_result_text():
    runner = FakeRunner(stdout=claude_json(structured_output=None))
    assert json.loads(provider(ClaudeCodeProvider, runner).complete("s", "u")) == GOOD


def test_claude_code_not_logged_in():
    runner = FakeRunner(stdout=claude_json(is_error=True, result="Not logged in · Please run /login"), returncode=1)
    with pytest.raises(AIError, match="/login"):
        provider(ClaudeCodeProvider, runner).complete("s", "u")


def test_claude_code_garbage_output():
    runner = FakeRunner(stdout="boom", stderr="crash", returncode=2)
    with pytest.raises(AIError, match="crash"):
        provider(ClaudeCodeProvider, runner).complete("s", "u")


def test_claude_code_timeout_and_missing_binary():
    runner = FakeRunner(exc=subprocess.TimeoutExpired("claude", 5))
    with pytest.raises(AIError, match="no respondió"):
        provider(ClaudeCodeProvider, runner).complete("s", "u")
    missing = ClaudeCodeProvider(path="/no/such/claude", runner=FakeRunner())
    with pytest.raises(AIError, match="No se encontró"):
        missing.complete("s", "u")


def test_claude_code_finds_vscode_bundled_binary(monkeypatch, tmp_path):
    exe = "claude.exe" if cli_agents.os.name == "nt" else "claude"
    binary = tmp_path / ".vscode" / "extensions" / "anthropic.claude-code-9.9.9-x" / "resources" / "native-binary" / exe
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    monkeypatch.setattr(cli_agents.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli_agents.shutil, "which", lambda name: None)
    assert ClaudeCodeProvider().find_binary() == str(binary)


def test_find_binary_prefers_path(monkeypatch):
    monkeypatch.setattr(cli_agents.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert CodexProvider().find_binary() == "/usr/bin/codex"
    monkeypatch.setattr(cli_agents.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_agents.Path, "home", lambda: Path("/nonexistent-home"))
    assert GeminiCLIProvider().find_binary() is None


# ------------------------------------------------------------------ Codex
def test_codex_reads_last_message_file(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    runner = FakeRunner(stdout="log noise", write_file=json.dumps(GOOD))
    result = provider(CodexProvider, runner, model="gpt-5").suggest(make_repo(), None, [], "es")
    assert result.description == "Gestor de repos"
    args = runner.args
    assert args[1:3] == ["exec", "--skip-git-repo-check"]
    assert args[-1] == "-"
    assert args[args.index("--model") + 1] == "gpt-5"
    assert "JSON" in runner.kwargs["input"]  # el system prompt va delante
    assert "OPENAI_API_KEY" not in runner.kwargs["env"]


def test_codex_error_suggests_login():
    runner = FakeRunner(stderr="Error: not logged in", returncode=1)
    with pytest.raises(AIError, match="codex login"):
        provider(CodexProvider, runner).complete("s", "u")


def test_codex_stdout_fallback():
    runner = FakeRunner(stdout=json.dumps(GOOD))
    assert json.loads(provider(CodexProvider, runner).complete("s", "u")) == GOOD


# ------------------------------------------------------------- Gemini CLI
def test_gemini_cli_json_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza")
    runner = FakeRunner(stdout=json.dumps({"response": json.dumps(GOOD), "stats": {}}))
    result = provider(GeminiCLIProvider, runner, model="gemini-2.5-pro").suggest(make_repo(), None, [], "es")
    assert result.topics == ["cli"]
    assert runner.args[1:3] == ["--output-format", "json"]
    assert runner.args[runner.args.index("--model") + 1] == "gemini-2.5-pro"
    assert "GEMINI_API_KEY" not in runner.kwargs["env"]


def test_gemini_cli_plain_text_and_errors():
    runner = FakeRunner(stdout=f"```json\n{json.dumps(GOOD)}\n```")
    assert "Gestor" in provider(GeminiCLIProvider, runner).complete("s", "u")
    runner = FakeRunner(stdout=json.dumps({"error": {"message": "auth required"}}))
    with pytest.raises(AIError, match="Login with Google"):
        provider(GeminiCLIProvider, runner).complete("s", "u")
    runner = FakeRunner(stdout="", returncode=0)
    with pytest.raises(AIError, match="ninguna respuesta"):
        provider(GeminiCLIProvider, runner).complete("s", "u")


# -------------------------------------------------------------- detección
def test_explicit_cli_provider(config):
    config.set("ai.provider", "claude-code")
    config.set("ai.model", "opus")
    config.set("ai.cli_path", "C:/tools/claude.exe")
    ai = get_ai_provider(config)
    assert isinstance(ai, ClaudeCodeProvider)
    assert ai.model == "opus" and ai.path == "C:/tools/claude.exe"
    assert ai.describe() == "Claude Code (suscripción, opus)"


def test_auto_prefers_subscription_over_env_keys(config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert detect_provider(config) == "claude"
    monkeypatch.setattr(cli_agents.CodexProvider, "is_available", lambda self: True)
    assert detect_provider(config) == "codex"
    monkeypatch.setattr(cli_agents.ClaudeCodeProvider, "is_available", lambda self: True)
    assert detect_provider(config) == "claude-code"


def test_auto_explicit_config_key_beats_subscription(config, monkeypatch):
    monkeypatch.setattr(cli_agents.ClaudeCodeProvider, "is_available", lambda self: True)
    config.set("ai.api_key", "AIzaXYZ")
    assert detect_provider(config) == "gemini"
