"""Test del generatore di puzzle e del ripasso Leitner (documentazione.txt §7.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gmvibe.models import (
    GameAnalysis,
    MoveRow,
    PositionFeatures,
    Puzzle,
    PuzzleConfig,
    PVLine,
    SideFeatures,
)
from gmvibe.profile.puzzles import (
    box_due,
    generate_puzzles,
    is_due,
    next_box,
    solve,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
INTERVALS = [1, 3, 7, 16, 35]


def _row(**kw) -> MoveRow:
    base = dict(ply=10, move_number=5, color="white", san="Qh5", uci="d1h5",
                fen_before="r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1",
                fen_after="r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/5N2/PPPP1PPP/RNB1K2R b KQkq - 0 1")
    base.update(kw)
    return MoveRow(**base)


def _analysis(rows) -> GameAnalysis:
    return GameAnalysis(rows=rows)


# --------------------------------------------------------------------------- #
# Generazione
# --------------------------------------------------------------------------- #
def test_generates_puzzle_from_significant_error():
    row = _row(label="errore", error_label="tattica_mancata", best_move_uci="f3g5",
               best_move_san="Ng5", winpct_loss=25.0,
               deep_lines=[PVLine(rank=1, move_uci="f3g5", move_san="Ng5", score_cp=120),
                           PVLine(rank=2, move_uci="c4f7", move_san="Bxf7+", score_cp=80)])
    puzzles = generate_puzzles(_analysis([row]), user_id="u", game_id=7)
    assert len(puzzles) == 1
    p = puzzles[0]
    assert p.solution_uci == "f3g5"          # soluzione = mossa del motore, mai dell'LLM
    assert p.solution_san == "Ng5"
    assert p.fen == row.fen_before
    assert p.theme == "tattica_mancata"
    assert "Ng5" in p.explanation and "25.0" in p.explanation
    assert "c4f7" in p.alternatives          # alternativa dai deep_lines


def test_below_threshold_not_generated():
    row = _row(label="errore", error_label="tempo", best_move_uci="f3g5", winpct_loss=4.0)
    assert generate_puzzles(_analysis([row]), user_id="u",
                            config=PuzzleConfig(min_winpct_loss=8.0)) == []


def test_only_errore_or_svarione():
    ok = _row(label="ok", error_label=None, best_move_uci="f3g5", winpct_loss=30.0)
    imp = _row(label="imprecisione", error_label="tempo", best_move_uci="f3g5", winpct_loss=30.0)
    assert generate_puzzles(_analysis([ok, imp]), user_id="u") == []


def test_max_per_game_and_ordering():
    rows = [
        _row(ply=2 * i + 1, label="errore", error_label="tempo", best_move_uci="e2e4",
             winpct_loss=float(i) * 10)
        for i in range(1, 8)
    ]
    puzzles = generate_puzzles(_analysis(rows), user_id="u", config=PuzzleConfig(max_per_game=3))
    assert len(puzzles) == 3
    assert [p.ply for p in puzzles] == [15, 13, 11]  # i tre peggiori


def test_disabled_config_generates_nothing():
    row = _row(label="errore", error_label="tempo", best_move_uci="e2e4", winpct_loss=50.0)
    assert generate_puzzles(_analysis([row]), user_id="u",
                            config=PuzzleConfig(enabled=False)) == []


def test_explanation_uses_positional_facts():
    fb = PositionFeatures(white=SideFeatures(castled=True, king_pawn_shield=1,
                                             isolated_pawns=["d4"]))
    row = _row(label="svarione", error_label="sicurezza_re", best_move_uci="f3g5",
               best_move_san="Ng5", winpct_loss=18.0, features_before=fb)
    p = generate_puzzles(_analysis([row]), user_id="u")[0]
    assert "scudo del Re ridotto" in p.explanation
    assert "d4" in p.explanation


# --------------------------------------------------------------------------- #
# Leitner (funzioni pure)
# --------------------------------------------------------------------------- #
def test_next_box_moves_up_and_resets():
    assert next_box(1, True, INTERVALS) == 2
    assert next_box(2, True, INTERVALS) == 3
    assert next_box(4, False, INTERVALS) == 1        # sbagliato -> si ricomincia
    assert next_box(5, True, INTERVALS) == 5         # consolidato: resta in cima
    assert next_box(99, True, INTERVALS) == 5        # clamp


def test_box_due_intervals():
    assert box_due(1, INTERVALS, NOW) == NOW + timedelta(days=1)
    assert box_due(3, INTERVALS, NOW) == NOW + timedelta(days=7)
    assert box_due(5, INTERVALS, NOW) == NOW + timedelta(days=35)


def test_is_due():
    assert is_due(None, NOW) is True                                  # mai ripassato
    assert is_due((NOW - timedelta(hours=1)).isoformat(), NOW) is True
    assert is_due((NOW + timedelta(days=2)).isoformat(), NOW) is False
    assert is_due("non-una-data", NOW) is True                       # dato sporco -> dovuto


def test_box_due_is_naive_safe():
    naive = datetime(2026, 10, 2, 12, 0)
    assert box_due(1, INTERVALS, naive) == naive + timedelta(days=1)


# --------------------------------------------------------------------------- #
# Correzione
# --------------------------------------------------------------------------- #
def test_solve_accepts_solution_and_alternatives():
    p = Puzzle(user_id="u", fen="x", solution_uci="f3g5", alternatives=["c4f7"])
    assert solve("f3g5", p) is True
    assert solve("c4f7", p) is True
    assert solve("a2a3", p) is False
    assert solve("", p) is False


def test_solve_accepts_db_row():
    row = {"solution_uci": "f3g5", "alternatives_json": '["c4f7"]'}
    assert solve("c4f7", row) is True
    assert solve("h2h4", row) is False


# --------------------------------------------------------------------------- #
# Regressione: `review` non deve andare in traceback su EOF/Ctrl-D (fine input).
# --------------------------------------------------------------------------- #
def test_review_cli_eof_exits_cleanly(tmp_db, monkeypatch, capsys):
    import argparse

    import gmvibe.cli.review_cmd as review_cmd
    from gmvibe.models import Puzzle

    tmp_db.save_puzzle(Puzzle(
        user_id="default", ply=1, fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        solution_uci="e2e4", solution_san="e4", theme="test",
    ))
    monkeypatch.setattr(review_cmd, "Database", lambda: tmp_db)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(EOFError()))

    rc = review_cmd.run(argparse.Namespace(profile=None, limit=None))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Ripasso interrotto" in out
