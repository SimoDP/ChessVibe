"""Test del parsing PGN e dei clock (documentazione.txt §5.A)."""

from __future__ import annotations

from gmvibe.sources.pgn_loader import parse_pgn_text


def test_moves_and_headers(sample_short_text):
    g = parse_pgn_text(sample_short_text)
    assert g.white == "BiancoTest"
    assert g.black == "NeroTest"
    assert g.result == "1-0"
    assert len(g.moves) == 13
    assert g.moves[0].san == "e4"
    assert g.moves[0].color == "white"
    assert g.moves[1].san == "e5"
    assert g.moves[1].color == "black"
    assert g.moves[-1].san == "Nd5#"


def test_clock_extraction(sample_short_text):
    g = parse_pgn_text(sample_short_text)
    assert g.moves[0].clock_seconds == 300.0
    assert g.moves[1].clock_seconds is not None
    # il clock decresce
    assert g.moves[1].clock_seconds <= g.moves[0].clock_seconds


def test_clock_delta(sample_short_text):
    g = parse_pgn_text(sample_short_text)
    assert g.moves[0].clock_delta is None            # prima mossa: nessun riferimento
    assert g.moves[2].clock_delta is not None        # 3a semimossa: delta dal clock precedente
    assert g.moves[2].clock_delta >= 0


def test_no_clocks(sample_noclk_text):
    g = parse_pgn_text(sample_noclk_text)
    assert len(g.moves) == 10
    assert all(m.clock_seconds is None for m in g.moves)
    assert all(m.clock_delta is None for m in g.moves)
    assert g.result == "*"


def test_ply_and_move_numbers(sample_noclk_text):
    g = parse_pgn_text(sample_noclk_text)
    assert g.moves[0].ply == 1 and g.moves[0].move_number == 1
    assert g.moves[1].ply == 2 and g.moves[1].move_number == 1
    assert g.moves[2].ply == 3 and g.moves[2].move_number == 2


def test_setup_fen_respected(sample_endgame_text):
    """Una partita con SetUp/FEN inizia dalla posizione indicata, non dallo startpos."""
    import chess

    g = parse_pgn_text(sample_endgame_text)
    assert g.start_fen != chess.STARTING_FEN
    board = chess.Board(g.start_fen)
    assert len(board.piece_map()) <= 7
    # le mosse della partita sono legali a partire da start_fen
    for m in g.moves:
        board.push_uci(m.uci)
    assert g.moves[0].san == "Kf2"


def test_draw_result(sample_draw_text):
    g = parse_pgn_text(sample_draw_text)
    assert g.result == "1/2-1/2"
    assert len(g.moves) == 10
