"""Selezione dei candidati al pass profondo (documentazione.txt §5.C).

Passano al pass profondo (costoso) le posizioni che contano:
- tutte le mosse con calo di win% sopra una soglia bassa;
- le mosse che ATTRAVERSANO un confine di verdetto (vinto<->patta<->perso);
- le transizioni di fase (cambio donne, ingresso nel finale);
- le posizioni in cui il giocatore ha impiegato molto o pochissimo tempo.
"""

from __future__ import annotations

import chess

from ..models import CandidateSelectionConfig


def _crosses_verdict(winpct_before: float, winpct_after: float, cfg: CandidateSelectionConfig) -> bool:
    """Vero se la mossa ATTRAVERSA un confine di verdetto (es. da patta a vinto).

    Si richiede un cambio effettivo (``before`` e ``after`` su lati opposti del livello),
    non la semplice vicinanza: con la vicinanza, in una partita combattuta quasi ogni
    mossa cadeva a ridosso di 80/20 e il pass profondo finiva per coprire meta' partita.
    """
    lo, hi = min(winpct_before, winpct_after), max(winpct_before, winpct_after)
    return any(lo < level <= hi for level in cfg.verdict_levels)


def is_phase_transition(board_before: chess.Board, board_after: chess.Board) -> bool:
    """Cambio donne (una donna scompare) o ingresso nel finale (materiale pesante basso)."""
    queens_before = len(board_before.pieces(chess.QUEEN, chess.WHITE)) + len(
        board_before.pieces(chess.QUEEN, chess.BLACK)
    )
    queens_after = len(board_after.pieces(chess.QUEEN, chess.WHITE)) + len(
        board_after.pieces(chess.QUEEN, chess.BLACK)
    )
    if queens_after < queens_before:
        return True

    heavy_before = _heavy(board_before)
    heavy_after = _heavy(board_after)
    # ingresso nel finale: si scende sotto la soglia in questa mossa
    return heavy_before > 13 >= heavy_after


_HEAVY_VALUES = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _heavy(board: chess.Board) -> int:
    return sum(
        len(board.pieces(pt, color)) * val
        for pt, val in _HEAVY_VALUES.items()
        for color in (chess.WHITE, chess.BLACK)
    )


def select_for_deep_pass(rows: list, max_candidates: int) -> tuple[list, int]:
    """Limita le righe candidate al pass profondo (tetto al costo, §5.D).

    Ritorna ``(righe_scelte, scartate)``. Con ``max_candidates <= 0`` nessun limite.
    Le righe sono ordinate per calo di win% decrescente (poi per ply): si approfondiscono
    prima le mosse piu' dannose, cosi' il tempo resta prevedibile.
    """
    candidates = [r for r in rows if getattr(r, "is_candidate", False)]
    if not max_candidates or len(candidates) <= max_candidates:
        return candidates, 0
    candidates.sort(key=lambda r: (-(r.winpct_loss or 0.0), r.ply))
    return candidates[:max_candidates], len(candidates) - max_candidates


def is_candidate(
    *,
    winpct_before: float,
    winpct_after: float,
    clock_delta: float | None,
    board_before: chess.Board,
    board_after: chess.Board,
    cfg: CandidateSelectionConfig,
) -> tuple[bool, list[str]]:
    """Decide se la posizione va al pass profondo. Ritorna ``(candidato, motivi)``."""
    reasons: list[str] = []
    if winpct_before - winpct_after >= cfg.winpct_drop:
        reasons.append("calo_winpct")
    if _crosses_verdict(winpct_before, winpct_after, cfg):
        reasons.append("cambio_verdetto")
    if is_phase_transition(board_before, board_after):
        reasons.append("transizione_fase")
    if clock_delta is not None:
        if clock_delta >= cfg.clock_high_seconds:
            reasons.append("tempo_alto")
        elif clock_delta <= cfg.clock_low_seconds:
            reasons.append("tempo_basso")
    return bool(reasons), reasons
