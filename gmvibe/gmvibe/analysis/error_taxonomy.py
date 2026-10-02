"""Classificazione della *natura* dell'errore (documentazione.txt §5.G).

Prima in modo euristico/deterministico, poi (FASE 5) eventualmente raffinata dall'LLM.
Qui NON si usa Claude: le etichette vengono dai segnali del motore e dalle feature.

Etichette: tattica_mancata, pezzo_in_presa, valutazione_errata, piano_sbagliato,
profilassi_mancata, sicurezza_re, tecnica_conversione, tecnica_difesa, finale_tecnica,
tempo, teoria.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from ..models import ErrorLabel, ErrorTaxonomyConfig, PositionFeatures

VALUES = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}


@dataclass
class ErrorContext:
    """Tutti i segnali disponibili per etichettare un errore."""

    winpct_before: float
    winpct_after: float
    phase: str
    board_before: chess.Board
    board_after: chess.Board
    played_move: chess.Move
    best_pv_uci: list[str] = field(default_factory=list)   # linea migliore del motore
    low_depth_best_uci: str | None = None
    deep_best_uci: str | None = None
    clock_seconds: float | None = None
    clock_delta: float | None = None
    features_before: PositionFeatures | None = None
    features_after: PositionFeatures | None = None
    features_delta: dict[str, float] = field(default_factory=dict)


def see(board: chess.Board, move: chess.Move) -> int:
    """Static Exchange Evaluation semplificata: guadagno materiale netto della mossa.

    Stima il valore del materiale scambiato assumendo ricatture con il pezzo di minor
    valore. Sufficiente per distinguere "pezzo in presa" da scambi normali.
    """
    victim = board.piece_at(move.to_square)
    gain = VALUES.get(victim.piece_type, 0) if victim else 0
    attacker = board.piece_at(move.from_square)
    if attacker is None:
        return 0
    # il pezzo che cattura puo' essere ricatturato? Dopo la spinta tocca all'avversario,
    # quindi i ricatturanti sono del colore ``b.turn``.
    b = board.copy(stack=False)
    b.push(move)
    defenders = (
        b.attackers(b.turn, move.to_square) if b.piece_at(move.to_square) else chess.SquareSet()
    )
    if defenders:
        gain -= VALUES.get(attacker.piece_type, 0)
    return gain


def leaves_piece_en_prise(board_after: chess.Board) -> bool:
    """Vero se, dopo la mossa, il lato che ha mosso ha un pezzo catturabile gratis.

    Si guarda la risposta migliore dell'avversario: se esiste una cattura con SEE > 0
    (guadagno netto) allora la mossa ha lasciato materiale in presa.
    """
    side = board_after.turn
    for move in board_after.legal_moves:
        if board_after.is_capture(move) and see(board_after, move) > 0:
            return True
    return False


def _is_forcing_move(board: chess.Board, move: chess.Move) -> bool:
    return board.is_capture(move) or board.gives_check(move) or move.promotion is not None


def is_forcing_line(board: chess.Board, pv_uci: list[str], plies: int = 4) -> bool:
    """Vero se la linea inizia con mosse forzanti (catture/scacchi) entro ``plies``
    semimosse."""
    b = board.copy(stack=False)
    for i, uci in enumerate(pv_uci[:plies]):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return False
        if move not in b.legal_moves:
            return False
        if _is_forcing_move(b, move):
            return True
        b.push(move)
    return False


def _left_theory(ctx: ErrorContext) -> bool:
    # La teoria richiede l'Opening Explorer (FASE 4): qui e' sempre False.
    return False


def classify_error(ctx: ErrorContext, cfg: ErrorTaxonomyConfig | None = None) -> ErrorLabel:
    """Assegna la natura dell'errore (§5.G). Ordine di priorita' delle regole."""
    c = cfg or ErrorTaxonomyConfig()
    before, after = ctx.winpct_before, ctx.winpct_after
    loss = before - after

    # 1. tempo: zeitnot o troppo tempo su una mossa banale
    if ctx.clock_seconds is not None and ctx.clock_seconds <= c.zeitnot_seconds:
        return "tempo"
    if (
        ctx.clock_delta is not None
        and ctx.clock_delta >= c.banal_time_seconds
        and loss <= c.banal_winpct_margin
    ):
        return "tempo"

    # 2. uscita dalla teoria con perdita misurabile (richiede Explorer, FASE 4)
    if _left_theory(ctx) and loss > 0:
        return "teoria"

    # 3. conversione: posizione vinta che crolla
    if before >= c.conversion_winpct and after < 50.0:
        return "tecnica_conversione"

    # 4. difesa: posizione peggiore ma tenibile, poi persa
    if 50.0 > before >= c.defense_winpct_band and after < c.defense_winpct_band:
        return "tecnica_difesa"

    # 5. finale
    if ctx.phase == "finale":
        return "finale_tecnica"

    # 6. sicurezza del Re: forte variazione dello scudo di pedoni del lato che muove
    if ctx.features_delta:
        mover = "white" if ctx.board_before.turn == chess.WHITE else "black"
        shield_key = f"{mover}_king_shield"
        if abs(ctx.features_delta.get(shield_key, 0.0)) >= c.king_safety_delta:
            return "sicurezza_re"

    # 7. tattica mancata: la linea migliore e' forzante e la bassa profondita' non la vedeva
    if is_forcing_line(ctx.board_before, ctx.best_pv_uci, c.forcing_plies):
        missed = (
            ctx.low_depth_best_uci is not None
            and ctx.deep_best_uci is not None
            and ctx.low_depth_best_uci != ctx.deep_best_uci
        )
        if missed or loss >= 15.0:
            return "tattica_mancata"

    # 8. pezzo in presa / svista: dopo la mossa un pezzo e' catturabile gratis
    if leaves_piece_en_prise(ctx.board_after):
        return "pezzo_in_presa"

    mover = "white" if ctx.board_before.turn == chess.WHITE else "black"
    weaknesses_grew = ctx.features_delta.get(f"{mover}_pawn_weaknesses", 0.0) > 0

    # 9. profilassi mancata: la mossa migliore era silenziosa (non forzante) e la nostra
    #    struttura e' peggiorata -> si e' mancato di prevenire il piano avversario
    if weaknesses_grew and ctx.best_pv_uci:
        try:
            best_is_quiet = not _is_forcing_move(ctx.board_before, chess.Move.from_uci(ctx.best_pv_uci[0]))
        except ValueError:
            best_is_quiet = False
        if best_is_quiet:
            return "profilassi_mancata"

    # 10. piano sbagliato: nessuna tattica, la struttura peggiora lentamente
    if weaknesses_grew:
        return "piano_sbagliato"

    # 11. fallback: cattura/scambio giocato su valutazione sbagliata
    return "valutazione_errata"
