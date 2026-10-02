"""Test della classificazione e del rilevamento di fase (documentazione.txt §5.E)."""

from __future__ import annotations

import chess
import pytest

from gmvibe.analysis.metrics import (
    classify_move,
    detect_phase,
    game_accuracy,
    move_accuracy,
    non_pawn_material,
)
from gmvibe.models import ClassificationConfig, ErrorThresholds

TH = ErrorThresholds(imprecisione=5.0, errore=10.0, svarione=15.0)


def _cls(loss, before=50.0, after=None):
    if after is None:
        after = before - loss
    return classify_move(loss, TH, winpct_before=before, winpct_after=after)


def test_thresholds_boundaries():
    assert _cls(0.0) == "ok"
    assert _cls(4.9) == "ok"
    assert _cls(5.0) == "imprecisione"
    assert _cls(9.9) == "imprecisione"
    assert _cls(10.0) == "errore"
    assert _cls(14.9) == "errore"
    assert _cls(15.0) == "svarione"
    assert _cls(60.0) == "svarione"


def test_improving_move_is_ok():
    # winpct_loss negativo (mossa che migliora) -> ok
    assert _cls(-8.0) == "ok"


def test_decided_winning_not_flagged():
    # gia' vinto e resta vinto: un calo non e' un errore rilevante
    assert _cls(20.0, before=97.0, after=93.0) == "ok"


def test_decided_losing_not_flagged():
    assert _cls(20.0, before=3.0, after=1.0) == "ok"


def test_victory_thrown_still_flagged():
    # vinto schiacciante ma si scende sotto il margine: e' un errore reale
    assert _cls(40.0, before=96.0, after=56.0) == "svarione"


def test_reproducible():
    for loss in (2.0, 7.0, 12.0, 18.0, 30.0):
        assert _cls(loss) == _cls(loss)


def test_classification_config_override():
    cfg = ClassificationConfig(decided_winpct_high=99.0, decided_keep_winning=99.0)
    # con soglia 99, una posizione a 97 non e' "decisa"
    label = classify_move(20.0, TH, winpct_before=97.0, winpct_after=93.0, classification=cfg)
    assert label == "svarione"


# --------------------------------------------------------------------------- #
# Fase
# --------------------------------------------------------------------------- #
def test_detect_phase_opening():
    assert detect_phase(chess.Board(), in_book=True) == "apertura"


def test_detect_phase_endgame():
    # Re e pedoni: poco materiale pesante
    board = chess.Board("8/5ppp/8/8/8/8/4PPP1/6K1 w - - 0 1")
    assert non_pawn_material(board) == 0
    assert detect_phase(board, in_book=False) == "finale"


def test_detect_phase_middlegame():
    assert detect_phase(chess.Board(), in_book=False) == "mediogioco"


# --------------------------------------------------------------------------- #
# Accuratezza
# --------------------------------------------------------------------------- #
def test_move_accuracy_bounds():
    assert 0.0 <= move_accuracy(50.0) <= 100.0
    assert move_accuracy(0.0) > move_accuracy(20.0)
    assert move_accuracy(-5.0) == pytest.approx(move_accuracy(0.0))  # clamp


def test_game_accuracy_mean():
    assert game_accuracy([]) is None
    a = game_accuracy([0.0, 0.0])
    b = game_accuracy([20.0, 20.0])
    assert a > b
