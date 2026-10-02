"""Test della tablebase (documentazione.txt §5.K).

I test con rete sono marcati ``slow`` e si auto-skippano se Lichess non e' raggiungibile.
"""

from __future__ import annotations

import chess
import pytest

from gmvibe.analysis.tablebase import TablebaseProbe, piece_count, white_pov_category
from gmvibe.models import TablebaseConfig

pytestmark = pytest.mark.slow


def test_piece_count():
    assert piece_count(chess.Board()) == 32
    assert piece_count(chess.Board("8/8/8/8/4k3/8/4K3/8 w - - 0 1")) == 2


def test_disabled_returns_none():
    probe = TablebaseProbe(TablebaseConfig(enabled=False))
    assert probe.probe(chess.Board("8/8/8/8/4k3/8/4K3/8 w - - 0 1")) is None


def test_too_many_pieces_returns_none():
    probe = TablebaseProbe(TablebaseConfig(allow_network=False, syzygy_max_pieces=5))
    assert probe.probe(chess.Board()) is None


def _network_available() -> bool:
    probe = TablebaseProbe(TablebaseConfig())
    return probe.probe(chess.Board("8/8/8/8/4k3/8/4K3/8 w - - 0 1")) is not None


@pytest.mark.skipif(not _network_available(), reason="tablebase Lichess non raggiungibile")
def test_lichess_draw_kvk():
    probe = TablebaseProbe(TablebaseConfig())
    v = probe.probe(chess.Board("8/8/8/8/4k3/8/4K3/8 w - - 0 1"))
    assert v is not None
    assert v.category == "draw"
    assert v.source == "lichess"


@pytest.mark.skipif(not _network_available(), reason="tablebase Lichess non raggiungibile")
def test_lichess_win_kqk():
    # K+Q vs K: il Bianco (che muove) vince
    probe = TablebaseProbe(TablebaseConfig())
    v = probe.probe(chess.Board("8/8/8/8/4k3/8/4KQ2/8 w - - 0 1"))
    assert v is not None
    assert v.category == "win"
    assert v.dtz is not None


@pytest.mark.skipif(not _network_available(), reason="tablebase Lichess non raggiungibile")
def test_category_is_mover_pov():
    # con il Nero al tratto nella stessa posizione, il verdetto e' "loss" per chi muove
    probe = TablebaseProbe(TablebaseConfig())
    v = probe.probe(chess.Board("8/8/8/8/3k4/8/3K1Q2/8 b - - 0 1"))
    assert v is not None
    assert v.category in ("loss", "blessed_loss")


def test_white_pov_category_inverts():
    from gmvibe.models import TablebaseVerdict

    v = TablebaseVerdict(fen="x", pieces=2, category="win", source="lichess")
    board_black = chess.Board("8/8/8/8/4k3/8/4K3/8 b - - 0 1")
    board_white = chess.Board("8/8/8/8/4k3/8/4K3/8 w - - 0 1")
    assert white_pov_category(v, board_white) == "win"
    assert white_pov_category(v, board_black) == "loss"
