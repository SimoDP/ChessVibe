"""Test del Complexity Score (documentazione.txt §5.H)."""

from __future__ import annotations

import chess

from gmvibe.analysis.complexity import compute_complexity, counterintuitive_score
from gmvibe.models import ComplexityConfig

CFG = ComplexityConfig()


def _cc(lines, board=None, best=None, fast=None, deep=None, cfg=CFG):
    return compute_complexity(
        lines, config=cfg, board_before=board or chess.Board(), best_move=best,
        fast_best_uci=fast, deep_best_uci=deep,
    )


def test_score_bounds():
    for lines in ([90, 88, 87, 86], [95, 40, 30, 20], [50], [50, 50, 50, 50]):
        b = _cc(lines)
        assert 0.0 <= b.score <= 100.0


def test_solo_mossa_has_high_complexity():
    """"Solo mossa": grande gap, poche mosse ragionevoli -> punteggio alto."""
    b = _cc([95.0, 55.0, 50.0, 45.0])
    assert b.reasonable_moves == 1
    assert b.score > 50.0


def test_many_options_low_complexity():
    """Molte mosse equivalenti -> punteggio basso."""
    b = _cc([60.0, 59.0, 58.5, 58.0])
    assert b.reasonable_moves == 4
    assert b.score < 30.0


def test_gap_recorded():
    b = _cc([90.0, 60.0, 55.0, 50.0])
    assert b.gap_winpct == 30.0


def test_instability_factor():
    stable = _cc([80, 70, 60, 50], fast="e2e4", deep="e2e4")
    unstable = _cc([80, 70, 60, 50], fast="d2d4", deep="e2e4")
    assert unstable.instability == 1.0
    assert stable.instability == 0.0
    assert unstable.score > stable.score


def test_counterintuitive_quiet_with_capture_available():
    # La donna bianca puo' catturare in d5; la mossa "migliore" e' pero' un ritiro di re.
    board = chess.Board("7k/8/8/3q4/8/8/8/3Q3K w - - 0 1")
    quiet = chess.Move.from_uci("h1h2")
    capture = chess.Move.from_uci("d1d5")
    assert counterintuitive_score(board, quiet) == 1.0
    assert counterintuitive_score(board, capture) == 0.0


def test_counterintuitive_retreat():
    # Cavallo in e4 senza catture: un ritiro e' controintuitivo, un avanzamento no.
    board = chess.Board("7k/8/8/8/4N3/8/8/7K w - - 0 1")
    retreat = chess.Move.from_uci("e4d2")
    advance = chess.Move.from_uci("e4g5")
    assert counterintuitive_score(board, retreat) == 1.0
    assert counterintuitive_score(board, advance) == 0.0


def test_counterintuitive_none_move():
    assert counterintuitive_score(chess.Board(), None) == 0.0
