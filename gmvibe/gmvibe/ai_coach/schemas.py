"""Schema di output del Maestro virtuale (§6.3) e utilità JSON Schema.

L'LLM non produce Markdown libero: restituisce JSON conforme a ``CoachReport``.
Questo modulo definisce il modello (in ``..models``, unica fonte) e la conversione
in JSON Schema compatibile con ``output_config.format`` di Anthropic.

Limiti del JSON Schema di Anthropic (structured outputs):
- gli oggetti devono avere ``additionalProperties: false``;
- i vincoli numerici/di lunghezza (``minimum``, ``minLength``, …) non sono supportati.
``json_schema_for`` normalizza di conseguenza; la validazione dei vincoli rimossi
resta comunque lato client, tramite Pydantic, in ``validators``.
"""

from __future__ import annotations

from typing import Any

from ..models import (
    CitedVariation,
    CoachMoment,
    CoachReport,
    LLMUsage,
    OpeningComment,
)

__all__ = [
    "CitedVariation",
    "CoachMoment",
    "CoachReport",
    "OpeningComment",
    "LLMUsage",
    "json_schema_for",
    "parse_report",
]

# Parole chiave di JSON Schema non supportate da structured outputs: rimosse prima
# dell'invio, perché Pydantic le rivalida comunque lato client.
_UNSUPPORTED_KEYS = frozenset(
    {
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minProperties",
        "maxProperties",
        "pattern",
        "default",
        "examples",
        "title",
    }
)


def _normalize(node: Any) -> Any:
    """Rende un JSON Schema compatibile con structured outputs (ricorsivo)."""
    if isinstance(node, list):
        return [_normalize(x) for x in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _UNSUPPORTED_KEYS:
            continue
        if key == "required" and isinstance(value, list):
            out[key] = list(value)
        else:
            out[key] = _normalize(value)

    # additionalProperties: false su ogni oggetto; i campi opzionali restano comunque
    # dichiarati e li accettiamo come stringa vuota / lista vuota.
    if out.get("type") == "object" or "properties" in out:
        out.setdefault("additionalProperties", False)
        props = out.get("properties")
        if isinstance(props, dict):
            # anthropic structured outputs: tutti i campi dichiarati e in `required`
            out["required"] = list(props.keys())
    return out


def json_schema_for(model: type) -> dict[str, Any]:
    """JSON Schema (dict) del modello Pydantic, normalizzato per Anthropic."""
    return _normalize(model.model_json_schema())


def parse_report(raw: str | dict[str, Any]) -> CoachReport:
    """Valida il JSON grezzo dell'LLM in un ``CoachReport``.

    Accetta sia una stringa JSON sia un dict già decodificato.

    Raises:
        pydantic.ValidationError: se la struttura non è conforme.
        ValueError: se la stringa non è JSON valido.
    """
    if isinstance(raw, str):
        import json

        data = json.loads(raw)
    else:
        data = raw
    return CoachReport.model_validate(data)
