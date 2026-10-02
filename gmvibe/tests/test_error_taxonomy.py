"""Test della tassonomia degli errori (documentazione.txt §5.G)."""

from __future__ import annotations

import chess

from gmvibe.analysis import features as F
from gmvibe.analysis.error_taxonomy import (
    ErrorContext,
    classify_error,
    is_forcing_line,
    leaves_piece_en_prise,
    see,
)
from gmvibe.models import ErrorTaxonomyConfig

CFG = ErrorTaxonomyConfig()


def _ctx(**kw):
    """Contesto di default: mediogioco, nessun segnale particolare."""
    base = dict(
        winpct_before=60.0, winpct_after=30.0, phase="mediogioco",
        board_before=chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
        board_after=chess.Board("rnbqkbnr/pppp1ppp/8/4p3/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
        played_move=chess.Move.from_uci("e2e3"),
    )
    base.update(kw)
    return ErrorContext(**base)


# --------------------------------------------------------------------------- #
# SEE e pezzo in presa
# --------------------------------------------------------------------------- #
def test_see_free_capture():
    # Bianco cattura una donna nera indifesa con la torre
    board = chess.Board("7k/8/8/3q4/8/8/8/K2R4 w - - 0 1")
    move = chess.Move.from_uci("d1d5")
    assert see(board, move) == 9


def test_see_defended_capture():
    # Bianco cattura un pedone difeso da un pedone
    board = chess.Board("7k/8/5p2/4p3/3P4/8/8/4K3 w - - 0 1")
    # d4xe5 cattura pedone (1) ma il pedone bianco puo' essere ricatturato da f6
    val = see(board, chess.Move.from_uci("d4e5"))
    assert val <= 0


def test_leaves_piece_en_prise():
    # Dopo la mossa bianca, il nero puo' catturare una torre bianca gratis
    after = chess.Board("7k/8/8/8/8/8/8/K2R3q b - - 0 1")
    # la donna nera in h1 puo' catturare la torre d1 (indifesa)
    assert leaves_piece_en_prise(after) is True


def test_does_not_leave_piece_en_prise():
    board = chess.Board("7k/8/8/8/8/8/8/K2R4 w - - 0 1")
    assert leaves_piece_en_prise(board) is False


# --------------------------------------------------------------------------- #
# Linee forzanti
# --------------------------------------------------------------------------- #
def test_forcing_line_capture():
    board = chess.Board("7k/8/8/3q4/8/8/8/K2R4 w - - 0 1")
    assert is_forcing_line(board, ["d1d5"], plies=4) is True


def test_forcing_line_quiet():
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    assert is_forcing_line(board, ["e2e4", "e7e5"], plies=4) is False


def test_forcing_line_illegal_move():
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    assert is_forcing_line(board, ["a1a8"], plies=4) is False


# --------------------------------------------------------------------------- #
# Regole della tassonomia
# --------------------------------------------------------------------------- #
def test_tempo_zeitnot():
    label = classify_error(_ctx(clock_seconds=5.0), CFG)
    assert label == "tempo"


def test_tempo_banal_move():
    label = classify_error(
        _ctx(winpct_before=50.0, winpct_after=49.0, clock_delta=200.0), CFG
    )
    assert label == "tempo"


def test_tecnica_conversione():
    # vinto (>=80) che crolla sotto 50
    label = classify_error(_ctx(winpct_before=90.0, winpct_after=40.0), CFG)
    assert label == "tecnica_conversione"


def test_tecnica_difesa():
    # peggio ma tenibile (30) -> persa (10)
    label = classify_error(_ctx(winpct_before=30.0, winpct_after=10.0), CFG)
    assert label == "tecnica_difesa"


def test_finale_tecnica():
    label = classify_error(_ctx(phase="finale", winpct_before=60.0, winpct_after=20.0), CFG)
    assert label == "finale_tecnica"


def test_sicurezza_re_via_delta():
    ctx = _ctx(winpct_before=60.0, winpct_after=40.0)
    ctx.features_delta = {"white_king_shield": -3.0}
    # chi muove e' il bianco (board_before.turn == WHITE)
    assert classify_error(ctx, CFG) == "sicurezza_re"


def test_tattica_mancata():
    board = chess.Board("7k/8/8/3q4/8/8/8/K2R4 w - - 0 1")
    ctx = _ctx(
        winpct_before=60.0, winpct_after=30.0, board_before=board,
        best_pv_uci=["d1d5"],
        low_depth_best_uci="a1a2", deep_best_uci="d1d5",
    )
    assert classify_error(ctx, CFG) == "tattica_mancata"


def test_fallback_valutazione_errata():
    # nessuna tattica, nessuna struttura peggiorata -> fallback
    ctx = _ctx(winpct_before=60.0, winpct_after=45.0)
    ctx.features_delta = {"white_pawn_weaknesses": 0.0}
    assert classify_error(ctx, CFG) == "valutazione_errata"


def test_always_returns_a_label():
    """Ogni errore deve ricevere un'etichetta (mai None)."""
    for before in (95, 80, 60, 40, 25):
        for after in (5, 20, 45, 70):
            label = classify_error(_ctx(winpct_before=before, winpct_after=after), CFG)
            assert label is not None
