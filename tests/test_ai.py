from types import SimpleNamespace

import anthropic
import httpx
import pytest
from conftest import FakeAI, make_repo

from gitkeeper.ai import ClaudeProvider, GeminiProvider, OpenAIProvider, detect_provider, get_ai_provider
from gitkeeper.ai.prompt import (
    MAX_DESCRIPTION,
    README_LIMIT,
    SCHEMA,
    build_user_prompt,
    clean_description,
    parse_suggestion,
)
from gitkeeper.errors import AIError

GOOD = '{"description": "Una CLI para gestionar repos", "topics": ["CLI", "git hub"]}'


# ------------------------------------------------------------------ prompt
def test_build_prompt_contains_evidence():
    repo = make_repo("tool", description="old", language="Go", topics=["x"], fork=True)
    prompt = build_user_prompt(repo, "# Tool\nDoes things", ["main.go", "cmd/"], "en")
    assert "Write the description in English." in prompt
    assert "alice/tool" in prompt and "Main language: Go" in prompt
    assert "Current description: old" in prompt
    assert "This repository is a fork." in prompt
    assert "main.go, cmd/" in prompt
    assert "<readme>\n# Tool\nDoes things\n</readme>" in prompt


def test_build_prompt_without_readme_and_truncation():
    repo = make_repo()
    assert "no README" in build_user_prompt(repo, None, [], "es")
    long_prompt = build_user_prompt(repo, "x" * (README_LIMIT + 500), [f"f{i}" for i in range(100)], "es")
    assert "first part only" in long_prompt
    assert "x" * (README_LIMIT + 1) not in long_prompt
    assert "(+20 more)" in long_prompt
    assert "Spanish" in long_prompt


@pytest.mark.parametrize(
    "text",
    [
        GOOD,
        f"```json\n{GOOD}\n```",
        f"Aquí tienes:\n{GOOD}\nSaludos",
    ],
)
def test_parse_suggestion_formats(text):
    suggestion = parse_suggestion(text)
    assert suggestion.description == "Una CLI para gestionar repos"
    assert suggestion.topics == ["cli", "git-hub"]


@pytest.mark.parametrize("text", ["no json", "[1, 2]", '{"description": ""}', '{"topics": []}'])
def test_parse_suggestion_errors(text):
    with pytest.raises(AIError):
        parse_suggestion(text)


def test_parse_suggestion_tolerates_bad_topics():
    assert parse_suggestion('{"description": "x", "topics": "nope"}').topics == []
    assert parse_suggestion('{"description": "x", "topics": [1, "ok", null]}').topics == ["ok"]
    many = parse_suggestion('{"description": "x", "topics": ["a","b","c","d","e","f","g","h"]}')
    assert len(many.topics) == 6


def test_clean_description():
    assert clean_description('  "Hola\n  mundo"  ') == "Hola mundo"
    long = clean_description("palabra " * 100)
    assert len(long) <= MAX_DESCRIPTION and long.endswith("…")


def test_base_suggest_uses_prompt(fake_ai):
    suggestion = fake_ai.suggest(make_repo(), "readme", ["a.py"], "es")
    assert suggestion.description == "Generated description"
    system, user = fake_ai.prompts[0]
    assert "JSON" in system and "readme" in user


# ------------------------------------------------------------------ Claude
class FakeAnthropic:
    def __init__(self, response=None, error=None):
        self.kwargs = None
        self.response = response
        self.error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def claude_response(text=GOOD, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


def test_claude_request_shape():
    client = FakeAnthropic(claude_response())
    provider = ClaudeProvider(client=client)
    result = provider.suggest(make_repo(), None, [], "es")
    assert result.description == "Una CLI para gestionar repos"
    kwargs = client.kwargs
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert kwargs["output_config"]["effort"] == "low"
    assert kwargs["fallbacks"] == "default"
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["messages"][0]["role"] == "user"
    assert "thinking" not in kwargs and "temperature" not in kwargs


def test_claude_custom_model():
    client = FakeAnthropic(claude_response())
    ClaudeProvider(model="claude-haiku-4-5", client=client).complete("s", "u")
    assert client.kwargs["model"] == "claude-haiku-4-5"


@pytest.mark.parametrize("stop, msg", [("refusal", "rechaz"), ("max_tokens", "cortó")])
def test_claude_stop_reasons(stop, msg):
    provider = ClaudeProvider(client=FakeAnthropic(claude_response(stop_reason=stop)))
    with pytest.raises(AIError, match=msg):
        provider.complete("s", "u")


def test_claude_no_text_block():
    response = SimpleNamespace(stop_reason="end_turn", content=[])
    with pytest.raises(AIError, match="texto"):
        ClaudeProvider(client=FakeAnthropic(response)).complete("s", "u")


def _status_error(cls, status):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": "x"}})
    return cls("x", response=response, body=None)


@pytest.mark.parametrize(
    "cls, status, msg",
    [
        (anthropic.AuthenticationError, 401, "API key inválida"),
        (anthropic.NotFoundError, 404, "modelo no encontrado"),
        (anthropic.RateLimitError, 429, "límite"),
        (anthropic.InternalServerError, 500, "error 500"),
    ],
)
def test_claude_error_mapping(cls, status, msg):
    try:
        error = _status_error(cls, status)
    except TypeError:  # pragma: no cover - firma distinta en otra versión del SDK
        pytest.skip("firma de excepción no compatible")
    provider = ClaudeProvider(client=FakeAnthropic(error=error))
    with pytest.raises(AIError, match=msg):
        provider.complete("s", "u")


def test_claude_generic_error():
    provider = ClaudeProvider(client=FakeAnthropic(error=RuntimeError("boom")))
    with pytest.raises(AIError, match="boom"):
        provider.complete("s", "u")


# ------------------------------------------------------------------ Gemini
class FakeGemini:
    def __init__(self, text=GOOD, error=None):
        self.kwargs = None
        self.text = text
        self.error = error
        self.models = SimpleNamespace(generate_content=self.generate)

    def generate(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def test_gemini_request_shape():
    client = FakeGemini()
    result = GeminiProvider(client=client).suggest(make_repo(), "r", [], "es")
    assert result.topics == ["cli", "git-hub"]
    assert client.kwargs["model"] == "gemini-flash-latest"
    config = client.kwargs["config"]
    assert config["response_mime_type"] == "application/json"
    assert config["response_json_schema"] == SCHEMA
    assert "JSON" in config["system_instruction"]


def test_gemini_empty_and_errors():
    with pytest.raises(AIError, match="no devolvió"):
        GeminiProvider(client=FakeGemini(text=None)).complete("s", "u")
    err = RuntimeError("quota")
    err.code = 429
    with pytest.raises(AIError, match="límite"):
        GeminiProvider(client=FakeGemini(error=err)).complete("s", "u")


def test_gemini_real_sdk_config_is_valid():
    """El dict de config debe ser aceptado por el SDK real de google-genai."""
    from google.genai import types

    client = FakeGemini()
    GeminiProvider(client=client).complete("sys", "user")
    parsed = types.GenerateContentConfig.model_validate(client.kwargs["config"])
    assert parsed.response_mime_type == "application/json"


# ------------------------------------------------------------------ OpenAI
class FakeOpenAI:
    def __init__(self, text=GOOD, error=None):
        self.kwargs = None
        self.text = text
        self.error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        message = SimpleNamespace(content=self.text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_openai_request_shape():
    client = FakeOpenAI()
    OpenAIProvider(client=client).suggest(make_repo(), None, [], "es")
    assert client.kwargs["model"] == "gpt-5-mini"
    assert client.kwargs["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in client.kwargs["messages"]] == ["system", "user"]


def test_openai_compatible_requires_model():
    with pytest.raises(AIError, match="ai.model"):
        OpenAIProvider(base_url="http://localhost:11434/v1")
    provider = OpenAIProvider(model="llama3.2", base_url="http://localhost:11434/v1")
    assert "localhost" in provider.describe()
    assert provider.client.base_url.host == "localhost"


def test_openai_errors():
    err = RuntimeError("nope")
    err.status_code = 401
    with pytest.raises(AIError, match="inválida"):
        OpenAIProvider(client=FakeOpenAI(error=err)).complete("s", "u")
    with pytest.raises(AIError, match="vacía"):
        OpenAIProvider(client=FakeOpenAI(text="")).complete("s", "u")


# --------------------------------------------------------------- detección
def test_detect_none(config):
    assert detect_provider(config) is None
    with pytest.raises(AIError, match="ANTHROPIC_API_KEY"):
        get_ai_provider(config)


@pytest.mark.parametrize(
    "env, expected",
    [("ANTHROPIC_API_KEY", "claude"), ("GEMINI_API_KEY", "gemini"), ("GOOGLE_API_KEY", "gemini"),
     ("OPENAI_API_KEY", "openai")],
)
def test_detect_from_env(config, monkeypatch, env, expected):
    monkeypatch.setenv(env, "k")
    assert detect_provider(config) == expected


def test_detect_priority_claude_first(config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert detect_provider(config) == "claude"


@pytest.mark.parametrize(
    "key, expected", [("sk-ant-abc", "claude"), ("AIzaXYZ", "gemini"), ("sk-proj-1", "openai")]
)
def test_detect_from_config_key_prefix(config, key, expected):
    config.set("ai.api_key", key)
    assert detect_provider(config) == expected


def test_detect_explicit_and_none(config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    config.set("ai.provider", "gemini")
    assert detect_provider(config) == "gemini"
    config.set("ai.provider", "none")
    assert detect_provider(config) is None


def test_base_url_implies_openai(config):
    config.set("ai.base_url", "http://localhost:11434/v1")
    config.set("ai.model", "llama3")
    assert detect_provider(config) == "openai"
    provider = get_ai_provider(config)
    assert isinstance(provider, OpenAIProvider) and provider.model == "llama3"


def test_get_ai_provider_builds_each(config):
    config.set("ai.api_key", "sk-ant-x")
    config.set("ai.model", "claude-sonnet-5")
    provider = get_ai_provider(config)
    assert isinstance(provider, ClaudeProvider) and provider.model == "claude-sonnet-5"
    config.set("ai.provider", "gemini")
    assert isinstance(get_ai_provider(config), GeminiProvider)


def test_fake_ai_is_ai_provider():
    assert FakeAI().describe() == "Fake (fake-1)"
