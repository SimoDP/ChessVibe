"""Metriche per partita e per fase (documentazione.txt §5.E).

Include:
- classificazione della mossa in imprecisione / errore / svarione, con soglie che
  arrivano dal ``LevelConfig`` (dipendono dal livello, §4.2);
- gestione delle posizioni gia' decise (non si segnalano errori irrilevanti);
- rilevamento della fase (apertura / mediogioco / finale);
- accuratezza totale e per fase con la formula di Lichess.
"""

from __future__ import annotations

import chess

from ..metrics import accuracy_from_loss
from ..models import ClassificationConfig, ErrorThresholds, MoveLabel, Phase

_PIECE_VALUES = {
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}

# Soglia di materiale (senza pedoni e re) sotto cui siamo in finale.
_ENDGAME_NON_PAWN = 13


def classify_move(
    winpct_loss: float,
    thresholds: ErrorThresholds,
    *,
    winpct_before: float,
    winpct_after: float,
    classification: ClassificationConfig | None = None,
) -> MoveLabel:
    """Etichetta la mossa in base alla perdita in win% (§5.E).

    Le posizioni gia' decise non generano errori: sopra ``decided_winpct_high`` se si
    resta sopra ``decided_keep_winning`` (ancora vinto), e sotto ``decided_winpct_low``
    (gia' perso) la mossa e' "ok".
    """
    cfg = classification or ClassificationConfig()

    if winpct_before >= cfg.decided_winpct_high and winpct_after >= cfg.decided_keep_winning:
        return "ok"
    if winpct_before <= cfg.decided_winpct_low:
        return "ok"

    loss = max(0.0, winpct_loss)
    if loss >= thresholds.svarione:
        return "svarione"
    if loss >= thresholds.errore:
        return "errore"
    if loss >= thresholds.imprecisione:
        return "imprecisione"
    return "ok"


def non_pawn_material(board: chess.Board) -> int:
    """Valore del materiale pesante (N/B/R/Q) di entrambi i lati, in punti."""
    total = 0
    for piece_type, value in _PIECE_VALUES.items():
        total += len(board.pieces(piece_type, chess.WHITE)) * value
        total += len(board.pieces(piece_type, chess.BLACK)) * value
    return total


def detect_phase(board_before: chess.Board, in_book: bool) -> Phase:
    """Fase della posizione: apertura (teoria), finale (poco materiale), mediogioco.

    Deterministico: ``in_book`` se la posizione e' ancora nel database di apertura;
    altrimenti finale se il materiale pesante e' sotto la soglia; altrimenti mediogioco.
    """
    if in_book:
        return "apertura"
    if non_pawn_material(board_before) <= _ENDGAME_NON_PAWN:
        return "finale"
    return "mediogioco"


def move_accuracy(winpct_loss: float) -> float:
    """Accuratezza della mossa (formula Lichess, §5.E); clampata in [0, 100]."""
    return max(0.0, min(100.0, accuracy_from_loss(max(0.0, winpct_loss))))


def game_accuracy(losses: list[float]) -> float | None:
    """Accuratezza media della partita a partire dalle perdite per mossa."""
    if not losses:
        return None
    return sum(move_accuracy(loss) for loss in losses) / len(losses)
