"""Prompt caching Anthropic (feature nativa ``cache_control``).

Il prompt caching di Anthropic è un *prefix match*: si marca con
``cache_control={"type": "ephemeral"}`` l'ultimo blocco stabile del prompt (qui il
system), così le chiamate ripetute sullo stesso preambolo costano ~0,1x in lettura.

Questo modulo espone solo l'helper di costruzione dei blocchi system; la
strumentazione dei token (``cache_read_input_tokens`` / ``cache_creation_input_tokens``)
è in :mod:`gmvibe.ai_coach.usage`.

Riferimento: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
"""

from __future__ import annotations

from typing import Any

__all__ = ["cache_system", "cache_read_tokens"]

# TTL di default del prompt caching (5 minuti). Oltre, l'entry va riscritta.
DEFAULT_TTL_SECONDS = 300


def cache_system(system: str, *, enabled: bool = True) -> list[dict[str, Any]]:
    """Costruisce il campo ``system`` come blocco con cache se abilitato.

    Un solo blocco: il breakpoint sull'ultimo blocco system cache-a anche gli
    eventuali strumenti che lo precedono.
    """
    block: dict[str, Any] = {"type": "text", "text": system}
    if enabled:
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def cache_read_tokens(usage: Any) -> int:
    """Token serviti dalla cache nell'ultima risposta (0 se assente)."""
    value = getattr(usage, "cache_read_input_tokens", None)
    return int(value) if value is not None else 0
