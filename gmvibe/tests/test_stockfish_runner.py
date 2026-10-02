"""Test del runner Stockfish reale (marcati ``slow``).

Verificano i fatti API su cui si basa il codice: MultiPV, WDL, root_moves, mate.
Skippati automaticamente se il binario non e' presente.
"""

from __future__ import annotations

import chess
import pytest

from gmvibe.engine.stockfish_runner import StockfishRunner, find_stockfish

pytestmark = pytest.mark.slow

_HAS_ENGINE = find_stockfish() is not None
skip_no_engine = pytest.mark.skipif(not _HAS_ENGINE, reason="Stockfish non installato")


@skip_no_engine
def test_engine_id_and_version():
    with StockfishRunner() as runner:
        ident = runner.engine_id
        assert "Stockfish" in ident.get("name", "")
        assert "Stockfish" in runner.engine_version_key()


@skip_no_engine
def test_fast_pass_returns_one_line():
    with StockfishRunner() as runner:
        ev = runner.fast_pass(chess.Board(), depth=10)
        assert ev.multipv == 1
        assert len(ev.lines) == 1
        assert ev.lines[0].move_uci  # una mossa
        assert ev.turn == "white"


@skip_no_engine
def test_multipv_returns_many_lines():
    with StockfishRunner() as runner:
        ev = runner.analyse_position(chess.Board(), depth=10, multipv=4)
        assert len(ev.lines) == 4
        ranks = [ln.rank for ln in ev.lines]
        assert ranks == [1, 2, 3, 4]


@skip_no_engine
def test_wdl_present():
    """Con UCI_ShowWDL, info['wdl'] deve popolare i conteggi (§5.B)."""
    with StockfishRunner() as runner:
        ev = runner.fast_pass(chess.Board(), depth=10)
        wdl = ev.lines[0].wdl
        assert wdl is not None
        assert wdl.wins + wdl.draws + wdl.losses > 0


@skip_no_engine
def test_root_moves_scores_played_move():
    """root_moves deve restituire l'eval della mossa giocata, anche se non ottimale."""
    with StockfishRunner() as runner:
        board = chess.Board()
        move = chess.Move.from_uci("a2a3")  # mossa non ottimale
        ev = runner.analysed_move(board, move, depth=10)
        assert len(ev.lines) == 1
        assert ev.lines[0].move_uci == "a2a3"


@skip_no_engine
def test_mate_detection():
    with StockfishRunner() as runner:
        # Matto di una mossa
        board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
        ev = runner.analyse_position(board, depth=14, multipv=1)
        line = ev.lines[0]
        assert line.mate is not None
