"""Package ai_coach — livello LLM di GM-Vibe (FASE 5).

Principio: il motore calcola, il codice misura, Claude spiega.
Le risposte dell'LLM sono sempre verificate per legalità (python-chess)
prima di essere mostrate all'utente.

Pipeline (documentazione.txt §6):
    GameAnalysis + LevelConfig + profilo
        → select_critical_moments (§5.I)      -> moments.py
        → build_context (§6.1)                -> context.py
        → client LLM (complete_structured)    -> anthropic_client.py | openrouter_client.py
          (provider scelto da config/env)      -> client_factory.py
        → validate_report (§6.4)              -> validators.py
        → (una rigenerazione se invalido, poi fallback ai soli dati motore)
        → CoachReport                         -> coach.py

Qualunque sia il provider (Anthropic diretto o gateway OpenRouter), le varianti citate sono
sempre validate per legalità con python-chess prima di essere mostrate (§6.4).
"""

from __future__ import annotations

__all__ = [
    "anthropic_client",
    "cache",
    "client_factory",
    "coach",
    "context",
    "errors",
    "moments",
    "openrouter_client",
    "prompt_cache",
    "schemas",
    "usage",
    "validators",
    "prompts",
]
