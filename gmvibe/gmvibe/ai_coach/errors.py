"""Errori comuni dei client LLM (§6.5).

I client concreti (:class:`~gmvibe.ai_coach.anthropic_client.AnthropicClient`,
:class:`~gmvibe.ai_coach.openrouter_client.OpenRouterClient`) sollevano tutti una
sottoclasse di :class:`LLMClientError`. L'orchestrazione (:mod:`gmvibe.ai_coach.coach`)
cattura la base, cosi' il provider effettivo non trapela nel resto del codice.
"""

from __future__ import annotations

__all__ = ["LLMClientError"]


class LLMClientError(Exception):
    """Errore del layer LLM (rete, chiave mancante, risposta malformata)."""
