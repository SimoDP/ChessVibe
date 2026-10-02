"""Client wrapper per Anthropic Messages API (documentazione.txt §6.5).

Caratteristiche:
- **Modelli da config/.env**: i default vengono da ``levels.yaml`` (``coach``), ma si
  possono sovrascrivere da ambiente (``GMVIBE_COACH_MODEL`` / ``GMVIBE_COACH_MODEL_FAST``).
- **Prompt caching**: i blocchi stabili (system) ricevono ``cache_control`` quando
  abilitato, così le chiamate ripetute sulla stessa partita costano meno.
- **Output strutturato**: ``complete_structured`` usa ``output_config.format``
  (JSON Schema) — l'LLM non produce mai Markdown libero (§6.3).
- **Contabilità token**: ogni risposta porta un :class:`~gmvibe.models.LLMUsage`.

Segreto: ``anthropic_api_key`` viene da ``secrets.env``/keyring (``config.load_secrets``),
mai hardcoded, mai loggato. Il pacchetto ``anthropic`` è una dipendenza opzionale
(extra ``llm``): se assente, l'errore è esplicito.

Nota API: sui modelli recenti (Opus 4.8/4.7, Sonnet 5) i parametri di sampling
(``temperature``, ``top_p``, ``top_k``) sono rifiutati con 400: non li inviamo mai.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from .. import paths
from ..config import load_secrets
from ..models import LLMUsage
from .errors import LLMClientError
from .prompt_cache import cache_system
from .usage import usage_from_response

logger = logging.getLogger(__name__)

_ANTHROPIC_ENV = "ANTHROPIC_API_KEY"
ENV_MODEL_MAIN = "GMVIBE_COACH_MODEL"
ENV_MODEL_FAST = "GMVIBE_COACH_MODEL_FAST"

_DEFAULT_MODEL_MAIN = "claude-opus-4-8"
_DEFAULT_MODEL_FAST = "claude-haiku-4-5"


class AnthropicError(LLMClientError):
    """Errore del client Anthropic (rete, chiave mancante, risposta malformata)."""


@dataclass
class LLMResponse:
    """Risposta di una chiamata LLM: testo, modello e contabilità token."""

    text: str
    usage: LLMUsage
    model: str
    stop_reason: str | None = None


def resolve_models(
    model_main: str | None = None, model_fast: str | None = None
) -> tuple[str, str]:
    """Risolve (main, fast) da config -> ambiente -> default."""
    main = os.environ.get(ENV_MODEL_MAIN) or model_main or _DEFAULT_MODEL_MAIN
    fast = os.environ.get(ENV_MODEL_FAST) or model_fast or _DEFAULT_MODEL_FAST
    return main, fast


class AnthropicClient:
    """Wrapper leggero attorno all'SDK Anthropic."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        fast_model: str | None = None,
        timeout: float = 120.0,
        profile: str = "default",
    ) -> None:
        self.model, self.fast_model = resolve_models(model, fast_model)
        self.timeout = timeout
        self._profile = profile
        self._api_key: str | None = api_key
        self._client: Any | None = None  # import pigro: anthropic è dipendenza opzionale

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _load_secrets(self) -> None:
        if self._api_key:
            return
        try:
            secrets = load_secrets(self._profile)
            if secrets.anthropic_api_key is not None:
                self._api_key = secrets.anthropic_api_key.get_secret_value()
        except Exception as exc:  # pragma: no cover - dipende dal profilo
            raise AnthropicError(f"impossibile caricare i segreti Anthropic: {exc}") from exc

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        self._load_secrets()
        if not self._api_key:
            raise AnthropicError(
                f"anthropic_api_key non trovata. Impostare {_ANTHROPIC_ENV} "
                f"in {paths.secrets_path(self._profile).name} o nel keyring."
            )
        try:
            import anthropic
        except ImportError as exc:
            raise AnthropicError(
                "il pacchetto 'anthropic' non è installato. "
                "Installare l'extra: pip install -e \".[llm]\"."
            ) from exc
        self._client = anthropic.Anthropic(api_key=self._api_key, timeout=self.timeout)
        return self._client

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
        cache: bool = False,
    ) -> LLMResponse:
        """Chiamata testuale semplice. Ritorna testo + contabilità token."""
        return self._call(
            system=system, messages=messages,
            model=model or self.model, max_tokens=max_tokens,
            cache=cache, output_format=None,
        )

    def complete_structured(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        model: str | None = None,
        max_tokens: int = 8192,
        cache: bool = False,
    ) -> LLMResponse:
        """Chiamata con output JSON vincolato allo schema (§6.3)."""
        return self._call(
            system=system, messages=messages,
            model=model or self.model, max_tokens=max_tokens,
            cache=cache,
            output_format={"type": "json_schema", "schema": schema},
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
        cache: bool,
        output_format: dict[str, Any] | None,
    ) -> LLMResponse:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": cache_system(system, enabled=cache),
            "messages": messages,
        }
        if output_format is not None:
            kwargs["output_config"] = {"format": output_format}

        logger.debug("Anthropic call model=%s max_tokens=%d cache=%s", model, max_tokens, cache)
        try:
            resp = client.messages.create(**kwargs)
        except Exception as exc:
            raise AnthropicError(f"chiamata Anthropic fallita: {exc}") from exc

        if getattr(resp, "stop_reason", None) == "refusal":
            raise AnthropicError("la richiesta è stata rifiutata dai classificatori di sicurezza")

        text_parts = [
            block.text
            for block in resp.content
            if getattr(block, "type", None) == "text"
        ]
        text = "\n".join(text_parts)
        usage = usage_from_response(model, resp.usage)
        return LLMResponse(
            text=text,
            usage=usage,
            model=model,
            stop_reason=getattr(resp, "stop_reason", None),
        )
