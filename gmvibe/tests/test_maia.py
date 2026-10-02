"""Test del fattore 5 (Maia) del Complexity Score (§5.H, FASE 8A), offline.

Nessun Lc0/pesi reali: si usa un client Maia finto e si esercita (a) il parser tollerante
dell'output di Lc0, (b) il fattore 5 in ``compute_complexity`` e (c) la degradazione
pulita quando Maia non e' disponibile. Il test ``slow`` verifica Lc0 reale, se presente.
"""

from __future__ import annotations

import sys
from pathlib import Path

import chess
import pytest

from gmvibe.analysis.complexity import compute_complexity
from gmvibe.engine.maia import (
    MaiaClient,
    build_maia_client,
    find_lc0,
    maia_availability,
    parse_move_stats,
)
from gmvibe.models import ComplexityConfig, MaiaConfig

ROOT = Path(__file__).resolve().parent.parent

# Estratto rappresentativo di ``lc0 --verbose-move-stats`` (il formato varia per versione:
# il parser e' volutamente tollerante).
LC0_STATS = """
info string e2e4 (N: 120) (P: 21.34%) (Q: 0.03) (U: 0.14) (Q+U: 0.17) (V: 0.02)
info string g1f3 (N: 40) (P: 9.50%) (Q: 0.01) (U: 0.09) (Q+U: 0.10) (V: 0.01)
info string d2d4 (N: 5) (P: 2.00%) (Q: 0.00) (U: 0.05) (Q+U: 0.05) (V: 0.00)
bestmove e2e4
"""


# --------------------------------------------------------------------------- #
# Parser e disponibilità
# --------------------------------------------------------------------------- #
def test_parse_move_stats():
    probs = parse_move_stats(LC0_STATS)
    assert probs["e2e4"] == pytest.approx(0.2134)
    assert probs["g1f3"] == pytest.approx(0.095)
    assert probs["d2d4"] == pytest.approx(0.02)


def test_parse_move_stats_tolerant_shapes():
    # forma compatta "e2e4 21.34%" e "P: 5%" senza parentesi
    assert parse_move_stats("e2e4 21.34%")["e2e4"] == pytest.approx(0.2134)
    assert parse_move_stats("a2a4 P: 5%")["a2a4"] == pytest.approx(0.05)
    assert parse_move_stats("") == {}


def test_availability_without_lc0(tmp_path):
    cfg = MaiaConfig(enabled=True, lc0_path=str(tmp_path / "nope"), weights_dir=str(tmp_path))
    assert maia_availability(cfg) == (False, False)


def test_build_client_degrades_to_none(tmp_path):
    cfg = MaiaConfig(lc0_path=str(tmp_path / "nope"), weights_dir=str(tmp_path))
    assert build_maia_client(enabled=True, maia_kind="maia2", maia_model="maia2", config=cfg) is None
    # disabilitato esplicitamente
    assert build_maia_client(enabled=False, maia_kind="maia2", maia_model="maia2", config=cfg) is None
    # kind "none" (nessun modello risolto)
    assert build_maia_client(enabled=True, maia_kind="none", maia_model=None, config=cfg) is None


def test_find_lc0_env(monkeypatch, tmp_path):
    fake = tmp_path / "lc0"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("GMVIBE_LC0", str(fake))
    assert find_lc0() == str(fake)


# --------------------------------------------------------------------------- #
# Fattore 5 nel Complexity Score
# --------------------------------------------------------------------------- #
class FakeMaia:
    """Client Maia finto: probabilità umana configurabile per la mossa data."""

    def __init__(self, prob: float | None) -> None:
        self.prob = prob
        self.queries: list = []

    def human_move_prob(self, board, move_uci):
        self.queries.append((board.fen(), move_uci))
        return self.prob

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def _complexity(human_prob, weights_maia=1.0):
    board = chess.Board()
    return compute_complexity(
        [52.0, 51.0, 50.5], config=ComplexityConfig(),
        board_before=board, best_move=chess.Move.from_uci("e2e4"),
        fast_best_uci="e2e4", deep_best_uci="e2e4",
        human_prob=human_prob,
    )


def test_factor5_absent_without_maia():
    b = _complexity(None)
    assert b.maia_human_prob is None
    assert "Maia" not in b.note


def test_factor5_present_and_in_note():
    b = _complexity(0.05)  # mossa poco naturale → difficile
    assert b.maia_human_prob == pytest.approx(0.05)
    assert "Maia" in b.note and "5%" in b.note


def test_factor5_raises_score_for_unnatural_move():
    natural = _complexity(0.90).score     # mossa quasi ovvia
    unnatural = _complexity(0.01).score   # mossa molto poco naturale
    assert unnatural > natural


def test_factor5_weight_zero_disables_influence():
    """Con peso 0 il fattore non cambia il punteggio (ma resta registrato)."""
    a = compute_complexity(
        [52.0, 51.0], config=ComplexityConfig(weights={"gap": 2, "reasonable_moves": 2,
                                                       "instability": 1, "counterintuitive": 1,
                                                       "maia": 0.0}),
        board_before=chess.Board(), best_move=chess.Move.from_uci("e2e4"),
        fast_best_uci="e2e4", deep_best_uci="e2e4", human_prob=0.01,
    )
    b = compute_complexity(
        [52.0, 51.0], config=ComplexityConfig(weights={"gap": 2, "reasonable_moves": 2,
                                                       "instability": 1, "counterintuitive": 1,
                                                       "maia": 0.0}),
        board_before=chess.Board(), best_move=chess.Move.from_uci("e2e4"),
        fast_best_uci="e2e4", deep_best_uci="e2e4", human_prob=0.99,
    )
    assert a.score == b.score


# Scambio di donne: e' una transizione di fase, quindi garantisce un candidato al pass
# profondo anche con eval piatte (il FakeRunner non produce cali di win%). Serve a
# esercitare il pass profondo senza dipendere dalle soglie di eval.
_TINY_PGN = (
    '[Event "t"]\n[White "a"]\n[Black "b"]\n[Result "*"]\n\n'
    "1. e4 e5 2. Qh5 Nc6 3. Qxe5+ Qe7 4. Qxe7+ Bxe7 *\n"
)


def test_pipeline_uses_injected_maia(tmp_db, fake_runner):
    """Il pipeline interroga il client Maia per la mossa migliore se fornito.

    Partita minima e profondità bassa: il test resta veloce pur esercitando il pass
    profondo (dove nasce il Complexity Score).
    """
    from gmvibe.analysis.pipeline import analyze_game

    fake = FakeMaia(0.07)
    analysis, _ = analyze_game(
        _TINY_PGN, depth=4, deep=True, deep_depth=4, db=tmp_db,
        runner=fake_runner, maia_client=fake,
    )
    # il fattore 5 è stato applicato almeno a una riga con pass profondo
    deep_rows = [r for r in analysis.rows if r.complexity and r.complexity.maia_human_prob is not None]
    assert deep_rows, "nessuna riga con fattore Maia"
    assert fake.queries, "il client Maia non è stato interrogato"


def test_maia_is_a_protocol():
    """Il client finto soddisfa il Protocol (duck-typing documentato)."""
    assert isinstance(FakeMaia(0.5), MaiaClient)


@pytest.mark.slow
def test_lc0_real_if_available():
    """Con Lc0 e i pesi reali (env GMVIBE_LC0 + weights_dir) la policy si legge davvero."""
    from gmvibe import config as cfg

    maia_cfg = cfg.load_levels().maia
    m2, m1 = maia_availability(maia_cfg)
    if not (m2 or m1):
        pytest.skip("Lc0/pesi Maia non installati")
    client = build_maia_client(
        enabled=True, maia_kind="maia2" if m2 else "maia1",
        maia_model="maia2" if m2 else f"maia-{maia_cfg.maia1_buckets[0]}", config=maia_cfg,
    )
    with client:
        prob = client.human_move_prob(chess.Board(), "e2e4")
    assert prob is not None and 0.0 <= prob <= 1.0
