"""Costruzione del pacchetto di contesto per il Maestro virtuale (§6.1).

Claude riceve **solo** un pacchetto JSON strutturato: header partita, apertura,
i momenti critici (§5.I) con i dati del motore, profilo del giocatore, statistiche
di fase e tempo. **Mai il PGN intero senza dati motore** (documentazione.txt §6.1).
"""

from __future__ import annotations

import json
from typing import Any

from ..models import (
    CriticalMoment,
    GameAnalysis,
    LevelConfig,
    PlayerProfile,
)

__all__ = [
    "build_context",
    "render_user_prompt",
    "render_system_prompt",
    "context_json",
]


def _moment_payload(m: CriticalMoment) -> dict[str, Any]:
    """Un momento nel formato mostrato a Claude, senza campi vuoti inutili."""
    payload: dict[str, Any] = {
        "id": m.id,
        "ply": m.ply,
        "color": m.color,
        "mossa_giocata": m.san,
        "mossa_giocata_uci": m.uci,
        "fen_prima": m.fen_before,
        "fen_dopo": m.fen_after,
        "categoria": m.category,
        "etichetta": m.label,
    }
    if m.error_label:
        payload["tipo_errore_motore"] = m.error_label
    if m.best_move_san or m.best_move_uci:
        payload["mossa_migliore_motore"] = {
            "san": m.best_move_san,
            "uci": m.best_move_uci,
        }
    if m.winpct_before is not None:
        payload["winpct_prima"] = m.winpct_before
    if m.winpct_after is not None:
        payload["winpct_dopo"] = m.winpct_after
    if m.winpct_loss is not None:
        payload["calo_winpct"] = m.winpct_loss
    if m.score_cp is not None or m.mate is not None:
        payload["eval"] = {"cp": m.score_cp, "mate": m.mate}
    if m.clock_seconds is not None:
        payload["clock_secondi"] = m.clock_seconds
    if m.clock_delta is not None:
        payload["tempo_speso_secondi"] = m.clock_delta
    if m.phase:
        payload["fase"] = m.phase
    if m.complexity is not None:
        payload["complexity"] = m.complexity.model_dump(exclude_none=True)
    if m.features_before is not None:
        payload["feature_posizione"] = m.features_before.model_dump(exclude_none=True)
    if m.features_delta:
        payload["delta_feature"] = m.features_delta
    if m.deep_lines:
        # top-4 linee del motore: solo mosse, eval e WDL (fatti, non opinioni).
        payload["linee_motore"] = [
            line.model_dump(exclude_none=True, exclude={"pv_uci"})
            for line in m.deep_lines
        ]
    elif m.best_move_uci:
        # Il momento non e' passato dal pass profondo (es. uscita dalla teoria o mossa
        # tranquilla scelta come riempimento): si fornisce comunque la mossa migliore del
        # pass veloce, cosi' ogni momento porta con se' il fatto del motore (§6.1).
        payload["linee_motore"] = [{
            "rank": 1,
            "move_uci": m.best_move_uci,
            "move_san": m.best_move_san,
            "score_cp": m.score_cp,
            "mate": m.mate,
        }]
    if m.reasons:
        payload["motivi_selezione"] = m.reasons
    return payload


def build_context(
    analysis: GameAnalysis,
    moments: list[CriticalMoment],
    *,
    profile: PlayerProfile | None = None,
    level_config: LevelConfig | None = None,
    recurring_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """Costruisce il pacchetto JSON passato a Claude (§6.1)."""
    level_config = level_config or analysis.level_config

    context: dict[str, Any] = {
        "partita": {
            "bianco": analysis.white,
            "nero": analysis.black,
            "risultato": analysis.result,
            "headers": {
                k: analysis.headers[k]
                for k in ("Event", "Date", "TimeControl", "ECO", "Opening", "Termination")
                if k in analysis.headers
            },
        },
        "livello": None
        if level_config is None
        else {
            "rating": level_config.rating_input,
            "rating_tipo": level_config.rating_tipo,
            "rating_lichess_stimato": level_config.rating_lichess,
            "descrittore": level_config.descrittore_livello,
        },
        "giocatore": None
        if profile is None
        else {
            "nickname": profile.nickname,
            "debolezze_dichiarate": profile.debolezze_dichiarate,
            "repertorio": profile.repertorio.model_dump(exclude_none=True),
        },
        "apertura": None
        if analysis.opening is None
        else analysis.opening.model_dump(exclude_none=True),
        "accuratezza": {
            "totale": analysis.accuracy.model_dump(exclude_none=True),
            "per_fase": {
                phase: summary.model_dump(exclude_none=True)
                for phase, summary in analysis.accuracy_by_phase.items()
            },
        },
        "errori_per_colore": analysis.counts,
        "tempo": None
        if analysis.time is None
        else analysis.time.model_dump(exclude_none=True),
        "momenti_critici": [_moment_payload(m) for m in moments],
    }

    if analysis.endgame:
        context["finale_tablebase"] = [
            v.model_dump(exclude_none=True) for v in analysis.endgame
        ]
    if recurring_patterns:
        # §7 (FASE 7): pattern ricorrenti dalle partite precedenti. Qui può arrivare
        # una lista già pronta; in fase 5 è tipicamente vuota.
        context["pattern_ricorrenti"] = recurring_patterns

    return context


def context_json(context: dict[str, Any]) -> str:
    """Rende il contesto come JSON stabile e leggibile."""
    return json.dumps(context, ensure_ascii=False, indent=2, sort_keys=False)


def render_system_prompt(
    template: str,
    *,
    level_config: LevelConfig | None,
    profile: PlayerProfile | None,
) -> str:
    """Riempie i placeholder del system prompt (§6.2)."""
    if level_config is not None:
        rating_display = (
            f"{level_config.rating_input} {level_config.rating_tipo}"
            f" (≈ {level_config.rating_lichess} Lichess)"
        )
        descrittore = level_config.descrittore_livello
    else:
        rating_display = "livello non specificato"
        descrittore = "giocatore generico"
    lingua = profile.lingua_report if profile is not None else "it"
    return (
        template.replace("{rating_display}", rating_display)
        .replace("{descrittore_livello}", descrittore)
        .replace("{lingua_report}", lingua)
    )


def render_user_prompt(template: str, context: dict[str, Any]) -> str:
    """Sostituisce ``{{contesto_json}}`` nel template con il pacchetto JSON."""
    return template.replace("{{contesto_json}}", context_json(context))
