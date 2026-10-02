"""Scelta del client LLM in base al provider configurato (§6.5).

Un solo punto decide se usare la SDK Anthropic diretta o il gateway OpenRouter, così
l'orchestrazione (:mod:`gmvibe.ai_coach.coach`) resta indipendente dal fornitore.

Il provider viene da ``coach.provider`` in ``levels.yaml``, sovrascrivibile da ambiente
(``GMVIBE_COACH_PROVIDER``). I modelli vengono dalla sezione del provider scelto.
"""

from __future__ import annotations

import os

from .. import paths
from ..models import CoachConfig
from .anthropic_client import AnthropicClient
from .openrouter_client import OpenRouterClient

__all__ = ["build_coach_client", "resolve_provider"]

ENV_PROVIDER = "GMVIBE_COACH_PROVIDER"


def resolve_provider(config: CoachConfig) -> str:
    """Provider effettivo: ambiente (``GMVIBE_COACH_PROVIDER``) -> config -> default."""
    value = (os.environ.get(ENV_PROVIDER) or config.provider or "anthropic").strip().lower()
    return "openrouter" if value == "openrouter" else "anthropic"


def _secrets_profile(nickname: str | None) -> str:
    """Profilo da cui leggere i segreti (§4.4).

    Se il profilo del giocatore ha un ``secrets.env`` si usa quello (è lì che ``init``
    scrive le chiavi); altrimenti si ripiega su ``default``, preservando il comportamento
    storico. I segreti vivono solo su file/keyring, mai nel repo.
    """
    if nickname:
        try:
            if paths.secrets_path(nickname).exists():
                return nickname
        except OSError:  # pragma: no cover - path non risolvibile
            pass
    return "default"


def build_coach_client(config: CoachConfig, *, nickname: str | None = None) -> object:
    """Costruisce il client LLM (:class:`AnthropicClient` o :class:`OpenRouterClient`).

    Args:
        config: ``CoachConfig`` da ``levels.yaml``.
        nickname: profilo del giocatore (per risolvere i segreti); None -> ``default``.
    """
    profile = _secrets_profile(nickname)
    if resolve_provider(config) == "openrouter":
        orc = config.openrouter
        return OpenRouterClient(
            model=orc.model_main,
            fast_model=orc.model_fast,
            base_url=orc.base_url,
            site_url=orc.site_url,
            app_name=orc.app_name,
            timeout=orc.timeout_seconds,
            profile=profile,
        )
    return AnthropicClient(
        model=config.model_main,
        fast_model=config.model_fast,
        timeout=config.timeout_seconds,
        profile=profile,
    )
