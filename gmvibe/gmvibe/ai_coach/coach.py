"""Orchestrazione del Maestro virtuale (documentazione.txt §6).

Flusso:
    GameAnalysis → momenti critici (§5.I) → contesto JSON (§6.1)
        → chiamata LLM con output strutturato (§6.3)
        → validazione obbligatoria (§6.4)
        → se invalido: UNA rigenerazione con l'errore esatto
        → se ancora invalido: fallback ai soli dati del motore, con avviso.

Il client LLM è iniettabile (duck-typed: espone ``complete_structured`` / ``complete``)
così i test girano offline senza il pacchetto ``anthropic``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..fairplay import require_idle
from ..models import (
    CoachConfig,
    CoachMoment,
    CoachReport,
    CriticalMoment,
    GameAnalysis,
    LLMUsage,
    LevelConfig,
    OpeningComment,
    PlayerProfile,
)
from .anthropic_client import LLMResponse
from .client_factory import build_coach_client
from .context import build_context, render_system_prompt, render_user_prompt
from .errors import LLMClientError
from .moments import select_critical_moments
from .schemas import json_schema_for, parse_report
from .usage import add_usage
from .validators import ValidationIssue, ValidationResult, validate_report

logger = logging.getLogger(__name__)

__all__ = ["CoachError", "CoachResult", "coach_analysis", "engine_only_report"]

ENGINE_ONLY_WARNING = (
    "Il testo del Maestro virtuale non ha superato la validazione: vengono mostrati "
    "solo i dati del motore per i momenti selezionati."
)


class CoachError(Exception):
    """Errore nel layer LLM (client non disponibile, risposta illeggibile)."""


@dataclass
class CoachResult:
    """Esito del layer LLM per una partita."""

    report: CoachReport
    validation: ValidationResult
    usage: LLMUsage | None = None
    meta: dict = field(default_factory=dict)

    @property
    def validated(self) -> bool:
        """Vero se il report mostrato è passato dalla validazione (nessun fallback)."""
        return bool(self.meta.get("validated", False))


def _player_color(profile: PlayerProfile | None, analysis: GameAnalysis) -> str | None:
    """Determina il lato del giocatore dal nickname del profilo."""
    if profile is None:
        return None
    nick = profile.nickname.strip().lower()
    if analysis.white and analysis.white.strip().lower() == nick:
        return "white"
    if analysis.black and analysis.black.strip().lower() == nick:
        return "black"
    return None


def _category_title(m: CriticalMoment, analysis: GameAnalysis) -> str:
    labels = {
        "punto_di_svolta": "Punto di svolta",
        "errore_indotto": "Errore indotto",
        "mossa_eccellente": "Mossa eccellente",
        "occasione_mancata": "Occasione mancata",
        "transizione_strategica": "Transizione strategica",
        "uscita_dalla_teoria": "Uscita dalla teoria",
        "posizione_complessa": "Posizione complessa",
    }
    base = labels.get(m.category, m.category)
    nat = f" — {m.error_label}" if m.error_label else ""
    return f"{base}: {m.san}{nat}"


def engine_only_report(
    analysis: GameAnalysis, moments: list[CriticalMoment], *, warning: str | None = None
) -> CoachReport:
    """Report di ripiego coi soli dati del motore (§6.4).

    Nessuna variante citata: quindi banalmente conforme alle regole di legalità.
    """
    momenti = [
        CoachMoment(
            id=m.id,
            fen_prima=m.fen_before,
            mossa_giocata=m.san,
            mossa_migliore=m.best_move_san or "",
            titolo=_category_title(m, analysis),
            tipo_errore=m.error_label or m.category,
            spiegazione="Dati del motore per questo momento (nessuna spiegazione LLM validata).",
        )
        for m in moments
    ]
    note = f"\n\n{warning}" if warning else ""
    return CoachReport(
        sintesi_partita=(
            "Analisi del motore completata; il commento del Maestro virtuale non è "
            "disponibile per questa partita." + note
        ),
        apertura=OpeningComment(),
        momenti=momenti,
        fase_piu_debole=_weakest_phase(analysis),
    )


def _weakest_phase(analysis: GameAnalysis) -> str:
    """Fase con accuratezza aggregata più bassa (per il fallback)."""
    worst = None
    worst_val = None
    for phase, summary in analysis.accuracy_by_phase.items():
        vals = [v for v in (summary.bianco, summary.nero) if v is not None]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        if worst_val is None or avg < worst_val:
            worst, worst_val = phase, avg
    return worst or ""


def _messages(system_prompt: str, user_prompt: str) -> list[dict]:
    return [{"role": "user", "content": user_prompt}]


def _regeneration_messages(
    user_prompt: str, previous_text: str, error_text: str
) -> list[dict]:
    return [
        {"role": "user", "content": user_prompt},
        {"role": "assistant", "content": previous_text},
        {"role": "user", "content": (
            "La tua risposta è stata RIFIUTATA dalla validazione automatica per i "
            "seguenti motivi:\n"
            f"{error_text}\n\n"
            "Rigenera l'intero JSON correggendo esattamente questi errori. Ogni "
            "variante deve essere LEGALE a partire dal suo fen_partenza e citare solo "
            "FEN e momenti forniti. Rispondi solo col JSON conforme allo schema."
        )},
    ]


def coach_analysis(
    analysis: GameAnalysis,
    *,
    profile: PlayerProfile | None = None,
    level_config: LevelConfig | None = None,
    client: object | None = None,
    config: CoachConfig | None = None,
    system_prompt: str | None = None,
    user_template: str | None = None,
    recurring_patterns: list[str] | None = None,
) -> CoachResult:
    """Esegue il layer LLM su una partita già analizzata (§6).

    Args:
        client: oggetto con ``complete_structured``/``complete`` (default:
            :class:`AnthropicClient`). Nei test si inietta un finto client.
        config: ``CoachConfig`` (default dai ``levels.yaml`` o dai default del modello).

    Raises:
        CoachError: se il client non è disponibile (chiave mancante, pacchetto assente).
    """
    # §8.3: nemmeno il layer LLM puo' girare durante una partita live.
    require_idle(action="analisi col Maestro")
    cfg = config or CoachConfig()
    level_config = level_config or analysis.level_config
    player_color = _player_color(profile, analysis)

    moments = select_critical_moments(
        analysis,
        max_moments=cfg.max_moments,
        min_moments=cfg.min_moments,
        player_color=player_color,
    )
    analysis.moments = moments

    if not moments:
        report = engine_only_report(analysis, moments)
        result = CoachResult(
            report=report,
            validation=ValidationResult(),
            meta={"validated": False, "fallback": True, "reason": "nessun momento critico"},
        )
        analysis.coach = report
        analysis.coach_meta = result.meta
        return result

    if system_prompt is None or user_template is None:
        from .prompts import load_prompt

        system_prompt = system_prompt or load_prompt("system_it")
        user_template = user_template or load_prompt("coach_analysis")

    system = render_system_prompt(system_prompt, level_config=level_config, profile=profile)
    context = build_context(
        analysis, moments, profile=profile, level_config=level_config,
        recurring_patterns=recurring_patterns,
    )
    user_prompt = render_user_prompt(user_template, context)

    if client is None:
        client = build_coach_client(
            cfg, nickname=profile.nickname if profile is not None else None
        )

    schema = json_schema_for(CoachReport)
    try:
        report, usage, validation, meta = _generate_and_validate(
            client, cfg=cfg, system=system, user_prompt=user_prompt,
            schema=schema, moments=moments, analysis=analysis,
        )
    except LLMClientError as exc:
        raise CoachError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # Contratto: il layer LLM non deve MAI propagare eccezioni grezze ai chiamanti
        # (CLI/web catturano solo CoachError). Un client iniettato o un errore fuori dal
        # try interno del client (es. TimeoutError) va comunque incapsulato.
        raise CoachError(f"{type(exc).__name__}: {exc}") from exc

    if not validation.ok:
        # §6.4: mai mostrare testo non verificato.
        logger.warning("validazione fallita dopo la rigenerazione: fallback ai dati motore")
        report = engine_only_report(analysis, moments, warning=ENGINE_ONLY_WARNING)
        meta["fallback"] = True
        meta["validated"] = False
    else:
        meta["validated"] = True

    meta["momenti"] = len(moments)
    meta["varianti_legali"] = validation.legal_variations
    meta["varianti_totali"] = validation.total_variations
    meta["varianti_legali_pct"] = round(100.0 * validation.legal_ratio, 1)

    result = CoachResult(report=report, validation=validation, usage=usage, meta=meta)
    analysis.coach = report
    analysis.coach_usage = usage
    analysis.coach_meta = meta
    return result


def _generate_and_validate(
    client: object,
    *,
    cfg: CoachConfig,
    system: str,
    user_prompt: str,
    schema: dict,
    moments: list[CriticalMoment],
    analysis: GameAnalysis,
) -> tuple[CoachReport | None, LLMUsage | None, ValidationResult, dict]:
    """Prima chiamata + (se invalido) UNA rigenerazione con l'errore esatto (§6.4)."""
    # Il modello da chiedere è quello con cui è configurato il CLIENT: per Anthropic è
    # ``cfg.model_main`` (id nativo), per OpenRouter è lo slug del gateway (§6.5). Nei
    # test il client iniettato non ha ``.model`` -> si usa ``cfg.model_main``.
    request_model = getattr(client, "model", None) or cfg.model_main
    meta: dict = {"regenerated": False, "model": request_model}

    def call(messages: list[dict]) -> tuple[str, LLMUsage]:
        if cfg.use_structured_output and hasattr(client, "complete_structured"):
            resp: LLMResponse = client.complete_structured(
                system=system, messages=messages, schema=schema,
                model=request_model, max_tokens=cfg.max_tokens_main, cache=cfg.cache_enabled,
            )
        else:
            resp = client.complete(  # type: ignore[attr-defined]
                system=system, messages=messages,
                model=request_model, max_tokens=cfg.max_tokens_main, cache=cfg.cache_enabled,
            )
        return resp.text, resp.usage

    def evaluate(text: str) -> tuple[CoachReport | None, ValidationResult]:
        """Parsa e valida; un JSON non conforme è esso stesso un errore bloccante."""
        try:
            report = parse_report(text)
        except Exception as exc:
            issue = ValidationIssue(
                kind="schema", message=f"output non conforme allo schema: {exc}",
            )
            return None, ValidationResult(issues=[issue])
        return report, validate_report(report, moments)

    text, usage = call(_messages(system, user_prompt))
    report, validation = evaluate(text)

    attempts = 0
    while not validation.ok and attempts < cfg.regenerate_on_invalid:
        attempts += 1
        meta["regenerated"] = True
        logger.info("rigenerazione LLM (tentativo %d) per errori di validazione", attempts)
        text, extra = call(_regeneration_messages(user_prompt, text, validation.error_text()))
        usage = add_usage(usage, extra)
        report, validation = evaluate(text)

    return report, usage, validation, meta
