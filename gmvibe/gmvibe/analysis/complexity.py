"""Complexity Score composto (documentazione.txt §5.H).

Quanto era difficile trovare la mossa giusta. Fattori 1-4 (il fattore 5, Maia, e'
opzionale e arriva in FASE 8):

1. **Gap** tra la migliore e la seconda mossa (in win%).
2. **Mosse ragionevoli**: quante stanno entro ~5 punti di win% dalla migliore.
3. **Instabilita' in profondita'**: la migliore a bassa profondita' differisce da quella
   profonda (o l'eval oscilla).
4. **Controintuitivita'**: la migliore e' silenziosa, un ritiro o un sacrificio, contro
   l'euristica "cattura/attacca".

Output: punteggio 0-100 + scomposizione dei fattori, da passare a Claude.
"""

from __future__ import annotations

import chess

from ..models import ComplexityBreakdown, ComplexityConfig


def _is_retreat(board: chess.Board, move: chess.Move) -> bool:
    """Ritiro: il pezzo torna verso la propria meta (traverse basse per il Bianco)."""
    fr = chess.square_rank(move.from_square)
    to = chess.square_rank(move.to_square)
    return to < fr if board.turn == chess.WHITE else to > fr


def counterintuitive_score(board_before: chess.Board, best_move: chess.Move | None) -> float:
    """1.0 se la mossa migliore e' "silenziosa" (non cattura/scacco/promozione) e
    contro l'euristica cattura/attacca: esiste una cattura disponibile, o e' un ritiro.
    """
    if best_move is None:
        return 0.0
    if (
        board_before.is_capture(best_move)
        or board_before.gives_check(best_move)
        or best_move.promotion is not None
    ):
        return 0.0  # mossa forzante: intuitiva

    has_capture_available = any(board_before.is_capture(m) for m in board_before.legal_moves)
    if has_capture_available or _is_retreat(board_before, best_move):
        return 1.0
    return 0.0


def compute_complexity(
    lines_winpct: list[float],
    *,
    config: ComplexityConfig,
    board_before: chess.Board,
    best_move: chess.Move | None,
    fast_best_uci: str | None,
    deep_best_uci: str | None,
    human_prob: float | None = None,
) -> ComplexityBreakdown:
    """Calcola il Complexity Score (0-100) da linee in win% dal POV di chi muove.

    ``human_prob`` (fattore 5, opzionale): probabilita' che un umano del livello giochi la
    mossa migliore, da Maia. Se ``None`` (Maia non installato/disattivato) il fattore non
    entra nella media: il punteggio resta quello dei fattori 1-4 (§5.H).
    """
    ordered = sorted(lines_winpct, reverse=True)
    top = ordered[0] if ordered else 50.0
    second = ordered[1] if len(ordered) > 1 else top

    # Fattore 1: gap (0..~25 win% -> 0..1)
    gap = max(0.0, top - second)
    f_gap = min(1.0, gap / 25.0)

    # Fattore 2: mosse ragionevoli (1 -> 1.0, 4+ -> 0.0): poche mosse = "solo mossa"
    reasonable = sum(1 for w in ordered if top - w <= config.reasonable_margin_winpct)
    f_reasonable = max(0.0, 1.0 - (reasonable - 1) / 3.0)

    # Fattore 3: instabilita' in profondita'
    instability = 1.0 if (fast_best_uci and deep_best_uci and fast_best_uci != deep_best_uci) else 0.0

    # Fattore 4: controintuitivita'
    f_counter = counterintuitive_score(board_before, best_move)

    # Fattore 5 (Maia, opzionale): poco naturalezza umana = difficile. Entra solo se nota.
    has_maia = human_prob is not None
    f_maia = max(0.0, min(1.0, 1.0 - float(human_prob))) if has_maia else 0.0

    w = config.weights
    weight_sum = w.gap + w.reasonable_moves + w.instability + w.counterintuitive
    weighted = (
        w.gap * f_gap
        + w.reasonable_moves * f_reasonable
        + w.instability * instability
        + w.counterintuitive * f_counter
    )
    if has_maia:
        weight_sum += w.maia
        weighted += w.maia * f_maia
    if weight_sum <= 0:  # pragma: no cover - pesi di default positivi
        weight_sum = 1.0
    score = 100.0 * weighted / weight_sum

    note_parts = [
        f"gap {gap:.1f} win%",
        f"{reasonable} mosse ragionevoli",
    ]
    if instability:
        note_parts.append("mossa migliore instabile in profondita'")
    if f_counter:
        note_parts.append("mossa migliore silenziosa/controintuitiva")
    if has_maia:
        note_parts.append(f"Maia: mossa migliore giocata dal {float(human_prob):.0%} degli umani")

    return ComplexityBreakdown(
        gap_winpct=round(gap, 2),
        reasonable_moves=reasonable,
        instability=instability,
        counterintuitive=f_counter,
        maia_human_prob=round(float(human_prob), 4) if has_maia else None,
        score=round(score, 1),
        note="; ".join(note_parts),
    )
