"""Analisi del tempo (documentazione.txt §5.J).

Grafico clock vs valutazione: dove hai speso tempo, dove sei finito in zeitnot, dove hai
sbagliato a clock alto o basso. Mosse banali pensate troppo; mosse critiche giocate troppo
in fretta.

Particolarmente importante nel blitz; per le partite OTB a cadenza lunga si adatta la
logica: niente soglie di zeitnot da 3+0 (``zeitnot_seconds: null`` nel profilo cadenza).
"""

from __future__ import annotations

import re

from ..models import MoveRow, TimeAnalysisConfig, TimeMoveNote, TimeProfile, TimeSummary


def cadenza_from_timecontrol(header: str | None) -> str:
    """Classifica la cadenza dall'header ``TimeControl`` del PGN.

    Formato "base+incremento" in secondi (es. "300+3"). Con ``classica`` a stima per
    eccesso le cadenze lunghe. Senza informazioni si assume ``blitz`` (caso peggiore).
    """
    if not header:
        return "blitz"
    m = re.match(r"\s*(\d+)\s*(\+(\d+))?", header)
    if not m:
        return "blitz"
    base = int(m.group(1))
    # tempo stimato per giocatore = base + 40 * incremento (stima standard)
    inc = int(m.group(3)) if m.group(3) else 0
    estimated = base + 40 * inc
    if estimated < 180:
        return "blitz"       # bullet e blitz corto
    if estimated < 600:
        return "blitz"
    if estimated < 1500:
        return "rapid"
    return "classica"


def profile_for(cadenza: str, config: TimeAnalysisConfig) -> TimeProfile:
    if cadenza in config.profiles:
        return config.profiles[cadenza]
    # fallback: primo profilo configurato o default
    if config.profiles:
        return next(iter(config.profiles.values()))
    return TimeProfile()


def analyze_time(rows: list[MoveRow], cadenza: str, config: TimeAnalysisConfig) -> TimeSummary:
    """Statistiche di tempo e mosse notevoli (zeitnot, banali lente, critiche veloci)."""
    prof = profile_for(cadenza, config)
    times = {"white": [], "black": []}
    summary = TimeSummary(cadenza=cadenza)
    zeitnot = 0

    for row in rows:
        if row.clock_delta is not None:
            times[row.color].append(row.clock_delta)

        # zeitnot: clock basso DOPO la mossa (se il profilo lo prevede)
        if prof.zeitnot_seconds is not None and row.clock_seconds is not None:
            if row.clock_seconds <= prof.zeitnot_seconds:
                zeitnot += 1
                summary.notes.append(TimeMoveNote(
                    ply=row.ply, san=row.san, color=row.color,
                    kind="zeitnot", seconds=row.clock_seconds,
                ))

        if row.clock_delta is not None:
            # mossa banale pensata troppo: non candidata ma con molto tempo speso
            if not row.is_candidate and row.clock_delta >= prof.banal_time_seconds:
                summary.notes.append(TimeMoveNote(
                    ply=row.ply, san=row.san, color=row.color,
                    kind="banale_lenta", seconds=row.clock_delta,
                ))
            # mossa critica giocata troppo in fretta
            if row.is_candidate and row.clock_delta <= prof.critical_fast_seconds:
                summary.notes.append(TimeMoveNote(
                    ply=row.ply, san=row.san, color=row.color,
                    kind="critica_veloce", seconds=row.clock_delta,
                ))

    summary.zeitnot_moves = zeitnot
    summary.white_avg = _avg(times["white"])
    summary.black_avg = _avg(times["black"])
    summary.white_max = max(times["white"]) if times["white"] else None
    summary.black_max = max(times["black"]) if times["black"] else None
    return summary


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)
