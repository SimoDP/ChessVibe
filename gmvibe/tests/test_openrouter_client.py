"""Client OpenRouter (§6.5) — test offline con transport finto.

Nessuna chiamata di rete, nessuna chiave API richiesta: il transport iniettato cattura la
richiesta e restituisce una risposta predisposta. Si verifica il contratto OpenAI-compatibile
(system come primo messaggio, ``response_format`` json_schema strict), la contabilità token,
la gestione degli errori e la scelta del provider in ``client_factory``.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from gmvibe.ai_coach.client_factory import build_coach_client, resolve_provider
from gmvibe.ai_coach.errors import LLMClientError
from gmvibe.ai_coach.openrouter_client import (
    OpenRouterClient,
    OpenRouterError,
    resolve_openrouter_models,
)
from gmvibe.ai_coach.schemas import json_schema_for
from gmvibe.ai_coach.usage import usage_from_openai_response
from gmvibe.config import mask_secret
from gmvibe.models import CoachConfig, CoachReport, Secrets


class FakeTransport:
    """Transport iniettabile: cattura la richiesta e restituisce una risposta fissa."""

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url: str, headers: dict, body: bytes) -> dict:
        self.calls.append((url, headers, json.loads(body.decode("utf-8"))))
        return self.response


def _response(text: str = "ciao", *, finish: str = "stop", usage: dict | None = None) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 1200, "completion_tokens": 350},
    }


def _client(transport, **kw) -> OpenRouterClient:
    return OpenRouterClient(api_key="sk-or-secret-123456", transport=transport, **kw)


# --------------------------------------------------------------------------- #
# Richiesta (contratto OpenAI-compatibile)
# --------------------------------------------------------------------------- #
def test_complete_sends_system_first_and_auth_headers():
    t = FakeTransport(_response("risposta"))
    client = _client(t, model="anthropic/claude-opus-4.8")
    resp = client.complete(system="SISTEMA", messages=[{"role": "user", "content": "ciao"}],
                           max_tokens=4096)

    url, headers, body = t.calls[0]
    assert url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer sk-or-secret-123456"
    assert headers["Content-Type"] == "application/json"
    # il system è il PRIMO messaggio; i messaggi seguono nell'ordine dato
    assert body["messages"][0] == {"role": "system", "content": "SISTEMA"}
    assert body["messages"][1] == {"role": "user", "content": "ciao"}
    assert body["model"] == "anthropic/claude-opus-4.8"
    assert body["max_tokens"] == 4096
    assert "response_format" not in body
    assert resp.text == "risposta"
    assert resp.model == "anthropic/claude-opus-4.8"


def test_optional_identifying_headers_included_when_set():
    t = FakeTransport(_response())
    client = _client(t, site_url="https://example.test", app_name="GM-Vibe")
    client.complete(system="s", messages=[{"role": "user", "content": "x"}])
    _, headers, _ = t.calls[0]
    assert headers["HTTP-Referer"] == "https://example.test"
    assert headers["X-Title"] == "GM-Vibe"


def test_optional_headers_omitted_when_empty():
    t = FakeTransport(_response())
    client = _client(t, site_url="", app_name="")
    client.complete(system="s", messages=[{"role": "user", "content": "x"}])
    _, headers, _ = t.calls[0]
    assert "HTTP-Referer" not in headers and "X-Title" not in headers


def test_complete_structured_sends_strict_json_schema():
    schema = json_schema_for(CoachReport)
    t = FakeTransport(_response("{}"))
    client = _client(t)
    client.complete_structured(system="s", messages=[{"role": "user", "content": "x"}],
                               schema=schema, max_tokens=8192)
    _, _, body = t.calls[0]
    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == schema
    # lo schema passato è già normalizzato per lo structured output
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"].keys())


# --------------------------------------------------------------------------- #
# Contabilità token
# --------------------------------------------------------------------------- #
def test_usage_is_mapped_and_cost_estimated():
    t = FakeTransport(_response(usage={"prompt_tokens": 1200, "completion_tokens": 350}))
    resp = _client(t).complete(system="s", messages=[{"role": "user", "content": "x"}])
    assert resp.usage.input_tokens == 1200
    assert resp.usage.output_tokens == 350
    assert resp.usage.cost_usd > 0


def test_usage_reads_cached_tokens_when_present():
    usage = {"prompt_tokens": 1000, "completion_tokens": 100,
             "prompt_tokens_details": {"cached_tokens": 400}}
    u = usage_from_openai_response("anthropic/claude-opus-4.8", usage)
    assert u.cache_read_input_tokens == 400
    # i token in cache non vengono addebitati a prezzo pieno
    assert u.cost_usd > 0


# --------------------------------------------------------------------------- #
# Errori
# --------------------------------------------------------------------------- #
def test_refusal_finish_reason_raises():
    t = FakeTransport(_response("", finish="refusal"))
    with pytest.raises(OpenRouterError):
        _client(t).complete(system="s", messages=[{"role": "user", "content": "x"}])


def test_missing_choices_raises():
    t = FakeTransport({"choices": []})
    with pytest.raises(OpenRouterError):
        _client(t).complete(system="s", messages=[{"role": "user", "content": "x"}])


def test_missing_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr("gmvibe.ai_coach.openrouter_client.load_secrets",
                        lambda profile: Secrets())
    t = FakeTransport(_response())
    client = OpenRouterClient(transport=t)  # nessuna api_key
    with pytest.raises(OpenRouterError) as exc:
        client.complete(system="s", messages=[{"role": "user", "content": "x"}])
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_urllib_post_maps_http_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    client = _client(transport=None)  # forza il transport di default
    with pytest.raises(OpenRouterError) as exc:
        client.complete(system="s", messages=[{"role": "user", "content": "x"}])
    assert "HTTP 401" in str(exc.value)


def test_urllib_post_maps_network_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("dns nope")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    client = _client(transport=None)
    with pytest.raises(OpenRouterError):
        client.complete(system="s", messages=[{"role": "user", "content": "x"}])


def test_openrouter_error_is_llm_client_error():
    assert issubclass(OpenRouterError, LLMClientError)


def test_mask_secret_hides_value():
    # nessun segreto in chiaro nei log/messaggi
    assert "sk-or-secret-123456" not in mask_secret("sk-or-secret-123456")
    assert mask_secret("sk-or-secret-123456").endswith("3456")


# --------------------------------------------------------------------------- #
# Risoluzione modelli + factory
# --------------------------------------------------------------------------- #
def test_resolve_openrouter_models_env_override(monkeypatch):
    monkeypatch.setenv("GMVIBE_OPENROUTER_MODEL", "openai/gpt-x")
    monkeypatch.setenv("GMVIBE_OPENROUTER_MODEL_FAST", "deepseek/y")
    assert resolve_openrouter_models("a", "b") == ("openai/gpt-x", "deepseek/y")


def test_factory_selects_openrouter(monkeypatch, tmp_path):
    cfg = CoachConfig()
    cfg.provider = "openrouter"
    cfg.openrouter.model_main = "anthropic/claude-opus-4.8"
    client = build_coach_client(cfg, nickname="nessuno")
    assert isinstance(client, OpenRouterClient)
    assert client.model == "anthropic/claude-opus-4.8"


def test_factory_selects_anthropic_by_default():
    client = build_coach_client(CoachConfig(), nickname="nessuno")
    from gmvibe.ai_coach.anthropic_client import AnthropicClient

    assert isinstance(client, AnthropicClient)


def test_factory_provider_env_override(monkeypatch):
    cfg = CoachConfig()  # provider: anthropic
    monkeypatch.setenv("GMVIBE_COACH_PROVIDER", "openrouter")
    assert resolve_provider(cfg) == "openrouter"
    assert isinstance(build_coach_client(cfg), OpenRouterClient)
