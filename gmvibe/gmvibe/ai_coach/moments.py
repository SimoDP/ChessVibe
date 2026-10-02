"""Rilevatore di momenti critici (§5.I).

Seleziona **al massimo 5-7 momenti** per partita, ordinati per impatto, a partire
dalla ``GameAnalysis`` prodotta dal pipeline (fase 2-4). Ogni momento è autonomo:
contiene tutto ciò che serve al Maestro virtuale e nient'altro (§6.1), quindi il
PGN intero non viene mai inviato.

Categorie (documentazione.txt §5.I):
- ``punto_di_svolta``      — il vantaggio attraversa una soglia significativa di win%;
- ``errore_indotto``       — errore in posizione ad alto Complexity Score;
- ``mossa_eccellente``     — "solo mossa" o mossa controintuitiva trovata dal giocatore;
- ``occasione_mancata``    — vantaggio tattico/posizionale non sfruttato;
- ``transizione_strategica`` — cambio donne, ingresso nel finale, cambio di fase;
- ``uscita_dalla_teoria``  — prima mossa fuori dal database, con risultato peggiore.

Il colore del giocatore analizzato (``player_color``) filtra i momenti sul suo lato;
se assente si considerano entrambi i lati.
"""

from __future__ import annotations

import chess

from ..models import CriticalMoment, GameAnalysis, MoveRow

# Pesi base per categoria: gli errori del giocatore vengono per primi, poi le mosse
# eccellenti (da riconoscere sempre), poi le transizioni e l'uscita dalla teoria.
_BASE = {
    "errore_indotto": 100.0,
    "punto_di_svolta": 95.0,
    "occasione_mancata": 70.0,
    "mossa_eccellente": 60.0,
    "transizione_strategica": 30.0,
    "uscita_dalla_teoria": 20.0,
}

# Soglia di Complexity Score oltre la quale una posizione è "ad alto complexity"
# (per errore_indotto) o una mossa trovata è "solo mossa" (per mossa_eccellente).
_HIGH_COMPLEXITY = 55.0


def _winpct(winpct: float | None) -> float:
    return winpct if winpct is not None else 50.0


def _crosses_verdict(before: float, after: float, levels: list[float]) -> bool:
    lo, hi = min(before, after), max(before, after)
    return any(lo < level <= hi for level in levels)


def _queens_traded(fen_before: str, fen_after: str) -> bool:
    try:
        before = chess.Board(fen_before)
        after = chess.Board(fen_after)
    except (ValueError, KeyError):
        return False
    # SquareSet non ha .count in python-chess 1.999: si usa len().
    qb = len(before.pieces(chess.QUEEN, chess.WHITE)) + len(before.pieces(chess.QUEEN, chess.BLACK))
    qa = len(after.pieces(chess.QUEEN, chess.WHITE)) + len(after.pieces(chess.QUEEN, chess.BLACK))
    return qa < qb


def _row_moment(row: MoveRow, category: str, impact: float, reasons: list[str]) -> CriticalMoment:
    return CriticalMoment(
        id=f"m{row.move_number}{'w' if row.color == 'white' else 'b'}",
        ply=row.ply,
        color=row.color,
        san=row.san,
        uci=row.uci,
        fen_before=row.fen_before,
        fen_after=row.fen_after,
        category=category,
        impact=impact,
        label=row.label,
        error_label=row.error_label,
        best_move_san=row.best_move_san,
        best_move_uci=row.best_move_uci,
        winpct_before=row.winpct_before,
        winpct_after=row.winpct_after,
        winpct_loss=row.winpct_loss,
        score_cp=row.score_cp,
        mate=row.mate,
        clock_seconds=row.clock_seconds,
        clock_delta=row.clock_delta,
        phase=row.phase,
        complexity=row.complexity,
        features_before=row.features_before,
        features_delta=row.features_delta,
        deep_lines=row.deep_lines,
        reasons=reasons,
    )


def _categorize(row: MoveRow, prev: MoveRow | None, verdict_levels: list[float],
                exit_ply: int | None) -> tuple[str, float, list[str]] | None:
    """Classifica una riga in un momento critico, o None se non è un momento."""
    loss = row.winpct_loss if row.winpct_loss is not None else 0.0
    cx = row.complexity.score if row.complexity is not None else 0.0
    reasons: list[str] = []

    # 1. Errore rilevante (dal punto di vista di chi muove).
    if row.label in ("errore", "svarione"):
        if cx >= _HIGH_COMPLEXITY:
            reasons.append(f"errore in posizione complessa (cx {cx:.0f})")
            return "errore_indotto", _BASE["errore_indotto"] + loss, reasons
        if _crosses_verdict(_winpct(row.winpct_before), _winpct(row.winpct_after), verdict_levels):
            reasons.append("il verdetto della posizione cambia")
            return "punto_di_svolta", _BASE["punto_di_svolta"] + loss, reasons
        reasons.append("vantaggio non sfruttato")
        return "occasione_mancata", _BASE["occasione_mancata"] + loss, reasons

    # 2. Mossa eccellente: il giocatore trova la migliore in posizione complessa.
    played_is_best = (
        row.best_move_uci is not None and row.uci == row.best_move_uci and row.label == "ok"
    )
    if played_is_best and cx >= _HIGH_COMPLEXITY:
        reasons.append(f"mossa migliore trovata (cx {cx:.0f})")
        return "mossa_eccellente", _BASE["mossa_eccellente"] + cx, reasons

    # 3. Transizione strategica: cambio di fase o cambio donne.
    if prev is not None and row.phase != prev.phase:
        reasons.append(f"transizione di fase: {prev.phase} -> {row.phase}")
        return "transizione_strategica", _BASE["transizione_strategica"] + loss, reasons
    if _queens_traded(row.fen_before, row.fen_after):
        reasons.append("cambio delle donne")
        return "transizione_strategica", _BASE["transizione_strategica"] + loss, reasons

    # 4. Uscita dalla teoria con risultato peggiore.
    if exit_ply is not None and row.ply == exit_ply and loss >= 2.0:
        reasons.append("uscita dalla teoria con perdita misurabile")
        return "uscita_dalla_teoria", _BASE["uscita_dalla_teoria"] + loss, reasons

    return None


def select_critical_moments(
    analysis: GameAnalysis,
    *,
    max_moments: int = 7,
    min_moments: int = 1,
    player_color: str | None = None,
    verdict_levels: list[float] | None = None,
) -> list[CriticalMoment]:
    """Seleziona i momenti critici ordinati per impatto (§5.I).

    Args:
        analysis: analisi completa della partita.
        max_moments: tetto (5-7 come da spec).
        min_moments: minimo desiderato; se i momenti "forti" scarseggiano se ne
            aggiungono di deboli (mosse peggiori, alta complessità) per arrivarci.
        player_color: "white"/"black" per limitare i momenti al lato del giocatore;
            None = entrambi i lati.
        verdict_levels: soglie di win% per il cambio di verdetto (default 50/80/20).
    """
    verdict_levels = verdict_levels or [50.0, 80.0, 20.0]
    exit_ply = analysis.opening.exit_ply if analysis.opening is not None else None

    if player_color is not None:
        rows = [r for r in analysis.rows if r.color == player_color]
    else:
        rows = list(analysis.rows)

    candidates: list[CriticalMoment] = []
    for i, row in enumerate(rows):
        prev = rows[i - 1] if i > 0 else None
        hit = _categorize(row, prev, verdict_levels, exit_ply)
        if hit is not None:
            category, impact, reasons = hit
            candidates.append(_row_moment(row, category, impact, reasons))

    # Dedup per ply: vince la categoria a impatto maggiore.
    best_by_ply: dict[int, CriticalMoment] = {}
    for m in candidates:
        cur = best_by_ply.get(m.ply)
        if cur is None or m.impact > cur.impact:
            best_by_ply[m.ply] = m

    ordered = sorted(best_by_ply.values(), key=lambda m: (-m.impact, m.ply))

    # Riempi fino al minimo con le mosse più significative non ancora scelte.
    if len(ordered) < min_moments:
        chosen = {m.ply for m in ordered}
        # Si accettano anche righe senza Complexity (non passate dal pass profondo):
        # altrimenti una partita senza errori evidenti restava senza NESSUN momento e il
        # Maestro non aveva nulla da commentare. L'ordinamento preferisce comunque le
        # posizioni piu' complesse e con maggior perdita.
        fillers = sorted(
            (r for r in rows if r.ply not in chosen),
            key=lambda r: -(
                (r.complexity.score if r.complexity else 0.0) + (r.winpct_loss or 0.0)
            ),
        )
        for r in fillers:
            if len(ordered) >= min_moments:
                break
            # Categoria neutra: una mossa tranquilla aggiunta solo per raggiungere
            # min_moments NON e' un'occasione mancata (etichettarla cosi' farebbe
            # dire al Maestro che un vantaggio e' stato sprecato quando non c'era).
            ordered.append(_row_moment(r, "posizione_complessa", 0.0, ["momento di riempimento"]))

    selected = ordered[:max_moments]
    selected.sort(key=lambda m: m.ply)  # ordine cronologico per la narrazione
    return selected


def moment_ids(moments: list[CriticalMoment]) -> set[str]:
    """Set degli id dei momenti (per la validazione §6.4)."""
    return {m.id for m in moments}


def moment_fens(moments: list[CriticalMoment]) -> set[str]:
    """Set dei FEN "prima" dei momenti: solo questi possono essere citati dall'LLM."""
    return {m.fen_before for m in moments}


__all__ = [
    "select_critical_moments",
    "moment_ids",
    "moment_fens",
]
