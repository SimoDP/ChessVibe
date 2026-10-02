"""Ri-analisi idempotente (bug "analisi/puzzle duplicati").

Rifare l'analisi della stessa partita (stesso ``user_id`` + ``pgn_hash``) non deve creare
una nuova riga in ``analyses``, né PGN grezzi/errori/puzzle duplicati: la riga esistente
viene aggiornata. Lo stato di ripasso (Leitner) dei puzzle identici è conservato.

Offline: motore "rumoroso" iniettato, come in ``test_batch_store.py``.
"""

from __future__ import annotations

import hashlib
import random

import chess
import chess.pgn

from gmvibe import config as cfg
from gmvibe.analysis.pipeline import analyze_game
from gmvibe.analysis.store import persist_analysis, pgn_hash
from gmvibe.models import EngineEval, PVLine, WDL
from gmvibe.profile.puzzles import next_box


class NoisyRunner:
    """Motore finto con eval variabili per posizione: produce errori etichettati reali."""

    def engine_version_key(self) -> str:
        return "Noisy 1.0 (test)"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def analyse_position(self, board, *, depth=None, time=None, multipv=1, root_moves=None):
        lines = []
        for i, mv in enumerate(list(board.legal_moves)[:multipv]):
            digest = int(hashlib.sha256((board.fen() + mv.uci()).encode()).hexdigest(), 16)
            cp = (digest % 1600) - 800
            lines.append(PVLine(rank=i + 1, move_uci=mv.uci(), score_cp=cp,
                                wdl=WDL(wins=500, draws=400, losses=100), pv_uci=[mv.uci()]))
        return EngineEval(
            fen=board.fen(), depth=depth or 0, multipv=multipv, engine_name="Noisy",
            engine_version="Noisy 1.0 (test)", turn="white" if board.turn else "black",
            lines=lines,
        )


def make_game(seed: int, *, plies: int = 20, white: str = "t1900") -> str:
    rnd = random.Random(seed)
    board = chess.Board()
    game = chess.pgn.Game()
    game.headers["White"] = white
    game.headers["Black"] = "avversario"
    game.headers["Result"] = "*"
    node = game
    for _ in range(plies):
        move = rnd.choice(list(board.legal_moves))
        board.push(move)
        node = node.add_variation(move)
    return str(game)


def _persist(db, text: str, user_id: str = "t1900"):
    analysis, _ = analyze_game(text, deep=False, db=db, runner=NoisyRunner())
    return persist_analysis(db, user_id, pgn_text=text, analysis=analysis,
                            levels=cfg.load_levels(), nickname=user_id)


# --------------------------------------------------------------------------- #
# Dedup dell'analisi
# --------------------------------------------------------------------------- #
def test_reanalysis_reuses_same_analysis_id(tmp_db):
    text = make_game(1)
    first = _persist(tmp_db, text)
    second = _persist(tmp_db, text)
    assert second.reused is True
    assert first.reused is False
    assert second.analysis_id == first.analysis_id
    # una sola riga in analyses per (utente, hash)
    assert len(tmp_db.list_analyses("t1900")) == 1


def test_reanalysis_does_not_duplicate_imported_pgn(tmp_db):
    text = make_game(1)
    _persist(tmp_db, text)
    _persist(tmp_db, text)
    n = tmp_db.conn.execute(
        "SELECT COUNT(*) AS c FROM imported_games WHERE user_id=? AND pgn_hash=?",
        ("t1900", pgn_hash(text)),
    ).fetchone()["c"]
    assert n == 1


def test_reanalysis_does_not_duplicate_errors(tmp_db):
    text = make_game(1)
    first = _persist(tmp_db, text)
    _persist(tmp_db, text)
    assert len(tmp_db.list_errors("t1900")) == first.error_count


def test_reanalysis_does_not_duplicate_puzzles(tmp_db):
    text = make_game(1)
    first = _persist(tmp_db, text)
    assert first.puzzle_count >= 1
    _persist(tmp_db, text)
    assert len(tmp_db.list_puzzles("t1900")) == first.puzzle_count


# --------------------------------------------------------------------------- #
# Stato di ripasso conservato
# --------------------------------------------------------------------------- #
def test_reanalysis_preserves_puzzle_review(tmp_db):
    text = make_game(1)
    first = _persist(tmp_db, text)
    puzzle = tmp_db.list_puzzles("t1900")[0]
    intervals = cfg.load_levels().puzzles.leitner_intervals_days
    box = next_box(1, True, intervals)          # box 2
    tmp_db.apply_review(int(puzzle["id"]), box=box, due_at="2030-01-01T00:00:00+00:00",
                        correct=True)
    _persist(tmp_db, text)
    after = tmp_db.list_puzzles("t1900")
    assert len(after) == first.puzzle_count
    # stesso puzzle (fen + soluzione) → box e conteggi conservati
    same = next(p for p in after if p["fen"] == puzzle["fen"]
                and p["solution_uci"] == puzzle["solution_uci"])
    assert same["box"] == box
    assert same["times_seen"] == 1
    assert same["times_correct"] == 1
    assert same["due_at"] == "2030-01-01T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Coach azzerato + isolamento fra utenti
# --------------------------------------------------------------------------- #
def test_reanalysis_resets_stale_coach(tmp_db):
    text = make_game(1)
    first = _persist(tmp_db, text)
    tmp_db.save_coach(first.analysis_id, '{"sintesi_partita": "x"}', "{}")
    assert tmp_db.get_analysis(first.analysis_id)["coach_json"] is not None
    _persist(tmp_db, text)
    assert tmp_db.get_analysis(first.analysis_id)["coach_json"] is None


def test_different_users_keep_separate_rows(tmp_db):
    text = make_game(1)
    a = _persist(tmp_db, text, user_id="alice")
    b = _persist(tmp_db, text, user_id="bob")
    assert a.analysis_id != b.analysis_id
    assert len(tmp_db.list_analyses("alice")) == 1
    assert len(tmp_db.list_analyses("bob")) == 1
