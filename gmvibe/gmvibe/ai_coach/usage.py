"""Contabilità token e costo del layer LLM (§6.5).

Il costo è una **stima** basata sui prezzi pubblici per milione di token; serve a
tracciare il costo per partita, non a fatturare. I prezzi dei modelli vivono qui e
non nel codice di orchestrazione; aggiungere un modello nuovo significa aggiungere
una riga a ``_PRICES``.

Cache (prompt caching): una scrittura in cache costa ~1,25x l'input, una lettura
~0,1x (§ prompt caching Anthropic). Le tariffe sono applicate di conseguenza.
"""

from __future__ import annotations

from ..models import LLMUsage

# (input_usd_per_1M, output_usd_per_1M). I vecchi modelli restano per riproducibilità
# delle analisi già salvate.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # Slug OpenRouter dei modelli Claude (prezzi Anthropic; OpenRouter non applica
    # ricarichi sui modelli Anthropic). Altri slug usano il fallback prudente.
    "anthropic/claude-opus-4.8": (5.0, 25.0),
    "anthropic/claude-opus-4.7": (5.0, 25.0),
    "anthropic/claude-sonnet-5": (3.0, 15.0),
    "anthropic/claude-sonnet-4.6": (3.0, 15.0),
    "anthropic/claude-haiku-4.5": (1.0, 5.0),
}

_CACHE_WRITE_MULT = 1.25
_CACHE_READ_MULT = 0.10


def price_for(model: str) -> tuple[float, float]:
    """Prezzi (input, output) per 1M token del modello; fallback prudente = Opus."""
    return _PRICES.get(model, _PRICES["claude-opus-4-8"])


def estimate_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> float:
    """Stima del costo in USD della chiamata."""
    in_price, out_price = price_for(model)
    per_token_in = in_price / 1_000_000.0
    per_token_out = out_price / 1_000_000.0
    return round(
        input_tokens * per_token_in
        + cache_creation_input_tokens * per_token_in * _CACHE_WRITE_MULT
        + cache_read_input_tokens * per_token_in * _CACHE_READ_MULT
        + output_tokens * per_token_out,
        6,
    )


def usage_from_response(model: str, usage) -> LLMUsage:
    """Costruisce un ``LLMUsage`` dall'oggetto ``usage`` di una risposta Anthropic."""

    def _get(obj, name: str) -> int:
        value = getattr(obj, name, None)
        return int(value) if value is not None else 0

    in_tok = _get(usage, "input_tokens")
    out_tok = _get(usage, "output_tokens")
    cache_read = _get(usage, "cache_read_input_tokens")
    cache_write = _get(usage, "cache_creation_input_tokens")
    return LLMUsage(
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        cost_usd=estimate_cost(
            model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


def usage_from_openai_response(model: str, usage: object) -> LLMUsage:
    """Costruisce un ``LLMUsage`` dall'oggetto ``usage`` di una risposta OpenAI-compatibile
    (OpenRouter). Mappa ``prompt_tokens``/``completion_tokens`` e, se il provider li
    riporta, i token serviti da cache (``prompt_tokens_details.cached_tokens``)."""

    def _get(obj, name: str):
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    def _int(value) -> int:
        return int(value) if value is not None else 0

    in_tok = _int(_get(usage, "prompt_tokens"))
    out_tok = _int(_get(usage, "completion_tokens"))
    details = _get(usage, "prompt_tokens_details")
    cache_read = _int(_get(details, "cached_tokens")) if details is not None else 0
    return LLMUsage(
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=0,
        cost_usd=estimate_cost(
            model,
            input_tokens=max(in_tok - cache_read, 0),
            output_tokens=out_tok,
            cache_read_input_tokens=cache_read,
        ),
    )


def add_usage(a: LLMUsage | None, b: LLMUsage | None) -> LLMUsage | None:
    """Somma due ``LLMUsage`` (es. più chiamate della stessa partita)."""
    if a is None:
        return b
    if b is None:
        return a
    return LLMUsage(
        model=a.model if a.model == b.model else f"{a.model}+{b.model}",
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_input_tokens=a.cache_read_input_tokens + b.cache_read_input_tokens,
        cache_creation_input_tokens=a.cache_creation_input_tokens + b.cache_creation_input_tokens,
        cost_usd=round(a.cost_usd + b.cost_usd, 6),
    )
