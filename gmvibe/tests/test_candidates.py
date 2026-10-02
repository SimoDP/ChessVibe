"""Test della selezione dei candidati (documentazione.txt §5.C)."""

from __future__ import annotations

import chess

from gmvibe.analysis.candidates import is_candidate, is_phase_transition
from gmvibe.models import CandidateSelectionConfig

CFG = CandidateSelectionConfig()


def _cand(**kw):
    base = dict(
        winpct_before=50.0, winpct_after=50.0, clock_delta=None,
        board_before=chess.Board(), board_after=chess.Board(), cfg=CFG,
    )
    base.update(kw)
    return is_candidate(**base)


def test_winpct_drop_triggers():
    ok, reasons = _cand(winpct_before=60.0, winpct_after=50.0)  # calo 10 >= 2
    assert ok and "calo_winpct" in reasons


def test_no_drop_no_candidate():
    # 55%: dentro la fascia di patta, lontano dai CONFINI di verdetto (80/20), calo
    # trascurabile, clock neutro. Il centro della fascia (~50%) NON e' un cambio di
    # verdetto: se lo fosse, ogni mossa di una partita equilibrata sarebbe candidata.
    ok, reasons = _cand(winpct_before=55.0, winpct_after=53.0)
    assert not ok


def test_verdict_crossing_80_triggers():
    # attraversa il confine vinto<->patta (80): 82 -> 78
    ok, reasons = _cand(winpct_before=82.0, winpct_after=78.0)
    assert ok and "cambio_verdetto" in reasons


def test_verdict_crossing_20_triggers():
    ok, reasons = _cand(winpct_before=22.0, winpct_after=18.0)
    assert ok and "cambio_verdetto" in reasons


def test_verdict_near_but_not_crossing_not_candidate():
    # 82 -> 81 e' VICINO a 80 ma non lo attraversa: non e' un cambio di verdetto.
    ok, reasons = _cand(winpct_before=82.0, winpct_after=81.0)
    assert not ok


def test_clock_high_triggers():
    ok, reasons = _cand(winpct_before=50.0, winpct_after=50.0, clock_delta=90.0)
    assert ok and "tempo_alto" in reasons


def test_clock_low_triggers():
    ok, reasons = _cand(winpct_before=50.0, winpct_after=50.0, clock_delta=0.5)
    assert ok and "tempo_basso" in reasons


def test_clock_normal_not_candidate():
    # 2s su una mossa: normale in blitz, non "pochissimo" -> non candidata da sola.
    ok, reasons = _cand(winpct_before=50.0, winpct_after=50.0, clock_delta=2.0)
    assert not ok


def test_small_drop_above_noise_not_candidate():
    # un calo di 3 punti e' nel rumore del pass veloce: non deve bastare da solo.
    ok, reasons = _cand(winpct_before=60.0, winpct_after=57.0)
    assert not ok


def test_balanced_game_not_all_candidates():
    """Regressione: in una partita equilibrata le mosse normali non sono tutte
    candidate (era il bug che faceva 'non finire mai' l'analisi)."""
    quiet = [
        _cand(winpct_before=51.0, winpct_after=50.0, clock_delta=2.0),
        _cand(winpct_before=49.0, winpct_after=50.0, clock_delta=3.0),
        _cand(winpct_before=52.0, winpct_after=48.0, clock_delta=2.0),
    ]
    assert all(not ok for ok, _ in quiet)


def test_phase_transition_queen_trade():
    before = chess.Board("rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    after = chess.Board("rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNB1KBNR b KQkq - 0 1")
    assert is_phase_transition(before, after) is True


def test_no_phase_transition():
    before = chess.Board()
    after = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1")
    assert is_phase_transition(before, after) is False


def test_select_for_deep_pass_caps_by_winpct_loss():
    """Il tetto limita il numero di posizioni e tiene le piu' dannose (calo maggiore)."""
    from types import SimpleNamespace

    from gmvibe.analysis.candidates import select_for_deep_pass

    rows = [
        SimpleNamespace(is_candidate=True, winpct_loss=3.0, ply=1),
        SimpleNamespace(is_candidate=True, winpct_loss=20.0, ply=2),
        SimpleNamespace(is_candidate=True, winpct_loss=10.0, ply=3),
        SimpleNamespace(is_candidate=False, winpct_loss=99.0, ply=4),
    ]
    selected, skipped = select_for_deep_pass(rows, 2)
    assert skipped == 1
    assert [r.ply for r in selected] == [2, 3]     # i due cali maggiori
    # nessun limite
    selected, skipped = select_for_deep_pass(rows, 0)
    assert skipped == 0 and len(selected) == 3     # solo i candidati
