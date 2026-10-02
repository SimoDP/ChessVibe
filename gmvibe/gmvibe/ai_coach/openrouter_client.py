"""Client per il gateway OpenRouter (API ``/chat/completions``, §6.5).

OpenRouter espone un'API **compatibile OpenAI**: si possono usare modelli Claude ma anche
di altri fornitori, con un'unica chiave (``OPENROUTER_API_KEY``). È l'alternativa al
percorso diretto :mod:`gmvibe.ai_coach.anthropic_client`; il provider si sceglie in config
(``coach.provider``) tramite :func:`gmvibe.ai_coach.client_factory.build_coach_client`.

Caratteristiche:
- **Nessuna dipendenza nuova**: HTTP con ``urllib`` (come ``lichess_live/board_api.py``),
  con un **transport iniettabile** per i test offline.
- **Output strutturato**: ``response_format`` con ``json_schema`` (``strict: true``),
  riusando :func:`gmvibe.ai_coach.schemas.json_schema_for` (già ``additionalProperties:
  false`` con tutti i campi in ``required``).
- **Prompt caching**: non gestito qui (varia per provider); la contabilità token riporta i
  token serviti da cache se il provider li espone.

Segreto: ``openrouter_api_key`` viene da ``secrets.env``/keyring (``config.load_secrets``),
mai hardcoded, mai loggato (``mask_secret`` in ogni messaggio d'errore).

Nota: se un provider non supporta ``json_schema`` strict, si degrada con
``coach.use_structured_output: false`` (prompt + validazione/rigenerazione §6.4).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable

from .. import paths
from ..config import load_secrets, mask_secret
from .anthropic_client import LLMResponse
from .errors import LLMClientError
from .usage import usage_from_openai_response

logger = logging.getLogger(__name__)

ENV_MODEL_MAIN = "GMVIBE_OPENROUTER_MODEL"
ENV_MODEL_FAST = "GMVIBE_OPENROUTER_MODEL_FAST"

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL_MAIN = "anthropic/claude-opus-4.8"
_DEFAULT_MODEL_FAST = "anthropic/claude-haiku-4.5"

# Nome dello schema inviato a OpenRouter (identifica il formato, non il contenuto).
_SCHEMA_NAME = "coach_report"

# Transport: (url, headers, body_bytes) -> dict già decodificato dal JSON.
Transport = Callable[[str, dict[str, str], bytes], dict]


class OpenRouterError(LLMClientError):
    """Errore del client OpenRouter (rete, chiave mancante, risposta malformata)."""


def resolve_openrouter_models(
    model_main: str | None = None, model_fast: str | None = None
) -> tuple[str, str]:
    """Risolve (main, fast) da ambiente -> config -> default."""
    main = os.environ.get(ENV_MODEL_MAIN) or model_main or _DEFAULT_MODEL_MAIN
    fast = os.environ.get(ENV_MODEL_FAST) or model_fast or _DEFAULT_MODEL_FAST
    return main, fast


class OpenRouterClient:
    """Client duck-typed come :class:`AnthropicClient` (``complete`` / ``complete_structured``)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        fast_model: str | None = None,
        base_url: str | None = None,
        site_url: str = "",
        app_name: str = "GM-Vibe",
        timeout: float = 120.0,
        profile: str = "default",
        transport: Transport | None = None,
    ) -> None:
        self.model, self.fast_model = resolve_openrouter_models(model, fast_model)
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.site_url = site_url
        self.app_name = app_name
        self.timeout = timeout
        self._profile = profile
        self._api_key: str | None = api_key
        # Il transport di default parla urllib; i test ne iniettano uno finto (offline).
        self._transport: Transport = transport or self._urllib_post

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _load_secrets(self) -> None:
        if self._api_key:
            return
        try:
            secrets = load_secrets(self._profile)
            if secrets.openrouter_api_key is not None:
                self._api_key = secrets.openrouter_api_key.get_secret_value()
        except Exception as exc:  # pragma: no cover - dipende dal profilo
            raise OpenRouterError(f"impossibile caricare i segreti OpenRouter: {exc}") from exc

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-Title"] = self.app_name
        return headers

    def _urllib_post(self, url: str, headers: dict[str, str], body: bytes) -> dict:
        """POST JSON di default; solleva :class:`OpenRouterError` su errore HTTP/rete."""
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:  # 4xx/5xx: il corpo contiene il motivo
            raw = exc.read()
            text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            raise OpenRouterError(f"HTTP {exc.code} da OpenRouter: {text[:500]}") from exc
        except urllib.error.URLError as exc:
            raise OpenRouterError(f"errore di rete verso OpenRouter: {exc.reason}") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise OpenRouterError(f"risposta OpenRouter non decodificabile: {exc}") from exc

    # ------------------------------------------------------------------ #
    # API pubblica
    # ------------------------------------------------------------------ #
    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 4096,
        cache: bool = False,  # noqa: ARG002 - il caching varia per provider, non gestito qui
    ) -> LLMResponse:
        """Chiamata testuale semplice. Ritorna testo + contabilità token."""
        return self._call(
            system=system, messages=messages,
            model=model or self.model, max_tokens=max_tokens, schema=None,
        )

    def complete_structured(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        model: str | None = None,
        max_tokens: int = 8192,
        cache: bool = False,  # noqa: ARG002
    ) -> LLMResponse:
        """Chiamata con output JSON vincolato allo schema (§6.3)."""
        return self._call(
            system=system, messages=messages,
            model=model or self.model, max_tokens=max_tokens, schema=schema,
        )

    # ------------------------------------------------------------------ #
    # Interno
    # ------------------------------------------------------------------ #
    def _call(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        schema: dict[str, Any] | None,
    ) -> LLMResponse:
        self._load_secrets()
        if not self._api_key:
            raise OpenRouterError(
                "openrouter_api_key non trovata. Impostare OPENROUTER_API_KEY "
                f"in {paths.secrets_path(self._profile).name} o nel keyring."
            )

        # OpenAI-compatibile: il system è il primo messaggio della conversazione.
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": _SCHEMA_NAME, "strict": True, "schema": schema},
            }

        url = f"{self.base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        logger.debug("OpenRouter call model=%s max_tokens=%d structured=%s key=%s",
                     model, max_tokens, schema is not None, mask_secret(self._api_key))
        data = self._transport(url, self._headers(self._api_key), body)

        choices = data.get("choices") or []
        if not choices:
            raise OpenRouterError(f"risposta OpenRouter senza 'choices': {str(data)[:300]}")
        choice = choices[0]
        finish = choice.get("finish_reason")
        if finish == "refusal":
            raise OpenRouterError("la richiesta è stata rifiutata dal provider")
        text = (choice.get("message") or {}).get("content") or ""
        usage = usage_from_openai_response(model, data.get("usage") or {})
        return LLMResponse(text=text, usage=usage, model=model, stop_reason=finish)
