"""Test di accettazione FASE 7: batch delle ultime 20 partite (§10).

Offline: motore "rumoroso" iniettato (eval variabili → errori etichettati) e apertura/
tablebase già disattivate dalla fixture autouse. Verifica che il report mostri
"già comparso in X partite" (§7) e che i puzzle siano generati (§7.1).
"""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

import chess
import chess.pgn

from gmvibe import config as cfg
from gmvibe.analysis.batch import analyze_many
from gmvibe.analysis.store import persist_analysis, pgn_hash
from gmvibe.models import EngineEval, PVLine, WDL
from gmvibe.profile.weaknesses import build_weakness_profile

ROOT = Path(__file__).resolve().parent.parent


class NoisyRunner:
    """Motore finto con eval variabili per posizione: produce errori etichettati reali."""

    def __init__(self) -> None:
        self.calls = 0

    def engine_version_key(self) -> str:
        return "Noisy 1.0 (test)"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def analyse_position(self, board, *, depth=None, time=None, multipv=1, root_moves=None):
        self.calls += 1
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
    """Partita legale deterministica e distinta per seed (pseudo-random walk)."""
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


def _batch(tmp_db, user_id="t1900", n=20, **kw):
    texts = [make_game(i) for i in range(n)]
    return analyze_many(texts, db=tmp_db, user_id=user_id, nickname=user_id,
                        deep=False, runner=NoisyRunner(), **kw)


# --------------------------------------------------------------------------- #
# Accettazione: 20 partite in batch
# --------------------------------------------------------------------------- #
def test_batch_of_20_games_analyzed(tmp_db):
    items = _batch(tmp_db)
    assert len(items) == 20
    assert all(it.ok for it in items)
    assert sum(it.error_count for it in items) > 0      # errori etichettati salvati
    assert sum(it.puzzle_count for it in items) > 0     # puzzle generati (§7.1)


def test_recurrence_shows_in_later_games(tmp_db):
    items = _batch(tmp_db)
    # L'ultima analisi deve avere almeno un errore già comparso in partite precedenti (§7).
    last = json.loads(tmp_db.analysis_json(items[-1].analysis_id))
    assert max((r["recurrence_games"] for r in last["rows"]), default=0) > 0
    # e il momento critico lo riporta per la UI
    assert any((m.get("recurrence_games") or 0) > 0 for m in last["moments"])


def test_profile_reports_x_su_20(tmp_db):
    _batch(tmp_db)
    p = build_weakness_profile(tmp_db, "t1900", levels=cfg.load_levels(), nickname="t1900")
    assert p.games_analyzed == 20
    assert p.label_frequency and p.label_frequency[0].games >= 1
    assert any("su 20 partite" in a for a in p.da_allenare)
    assert len(p.da_allenare) == 3


def test_rerun_skips_existing(tmp_db):
    _batch(tmp_db)
    again = _batch(tmp_db)
    assert all(it.skipped for it in again)
    assert all(it.analysis_id is None for it in again)


# --------------------------------------------------------------------------- #
# persist_analysis diretto
# --------------------------------------------------------------------------- #
def test_persist_analysis_result_counts(tmp_db):
    from gmvibe.analysis.pipeline import analyze_game

    text = make_game(1)
    analysis, _ = analyze_game(text, deep=False, db=tmp_db, runner=NoisyRunner())
    result = persist_analysis(tmp_db, "t1900", pgn_text=text, analysis=analysis,
                              levels=cfg.load_levels(), source="lichess", nickname="t1900")
    assert result.analysis_id > 0
    assert result.error_count == len([r for r in analysis.rows if r.label != "ok"])
    assert result.puzzle_count >= 1
    # la partita grezza è conservata, con la sorgente giusta
    imported = tmp_db.find_imported_by_hash("t1900", pgn_hash(text))
    assert imported is not None and imported["source"] == "lichess"


def test_persist_analysis_empty_pgn_not_saved(tmp_db):
    """Una partita senza mosse non genera errori/puzzle, ma viene comunque salvata."""
    from gmvibe.models import GameAnalysis

    empty = GameAnalysis(rows=[], result="*")
    result = persist_analysis(tmp_db, "u", pgn_text="[Event \"x\"]\n\n*",
                              analysis=empty, levels=cfg.load_levels())
    assert result.error_count == 0 and result.puzzle_count == 0


# --------------------------------------------------------------------------- #
# CLI: i sottocomandi della FASE 7 esistono e rispondono
# --------------------------------------------------------------------------- #
def test_new_cli_subcommands_help():
    for cmd in ("import", "profile", "puzzles", "review"):
        r = subprocess.run(
            [sys.executable, "-m", "gmvibe", cmd, "--help"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        )
        assert r.returncode == 0, r.stderr
    # analyze espone --save (§7)
    r = subprocess.run([sys.executable, "-m", "gmvibe", "analyze", "--help"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert "--save" in r.stdout
