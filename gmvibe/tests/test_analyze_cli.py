"""Test end-to-end della pipeline ``analyze`` (documentazione.txt §10 FASE 1)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from gmvibe.cli.analyze import analyze_game, render_table

ROOT = Path(__file__).resolve().parent.parent


def test_analyze_three_fixtures(tmp_db, fake_runner, fixture_dir):
    for name in ("sample_short.pgn", "sample_draw.pgn", "sample_noclk.pgn"):
        text = (fixture_dir / name).read_text(encoding="utf-8")
        analysis, stats = analyze_game(text, db=tmp_db, runner=fake_runner, depth=8)
        assert stats["moves"] == len(analysis.rows)
        assert len(analysis.rows) > 0
        # ogni riga ha eval/win% calcolati
        for row in analysis.rows:
            assert row.winpct_after is not None
            assert 0.0 <= row.winpct_after <= 100.0


def test_terminal_mate_is_win(tmp_db, fake_runner, sample_short_text):
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8)
    last = analysis.rows[-1]
    # Nd5# da matto: il lato che muove vince
    assert last.san == "Nd5#"
    assert last.winpct_after == pytest.approx(100.0)


def test_opening_name_assigned(tmp_db, fake_runner, sample_short_text):
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8)
    assert analysis.rows[0].opening_name is not None


def test_clock_propagated(tmp_db, fake_runner, sample_short_text):
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8)
    assert analysis.rows[0].clock_seconds == 300.0


def test_render_table_contains_headers(tmp_db, fake_runner, sample_short_text):
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8)
    out = render_table(analysis)
    assert "win%" in out
    assert "apertura" in out
    assert "e4" in out


def test_cli_entrypoint(tmp_db, sample_short_text, tmp_path, monkeypatch):
    """``python -m gmvibe analyze`` su un PGN reale (richiede Stockfish)."""
    from gmvibe.engine.stockfish_runner import find_stockfish

    if find_stockfish() is None:
        pytest.skip("Stockfish non disponibile")

    pgn = tmp_path / "g.pgn"
    pgn.write_text(sample_short_text, encoding="utf-8")
    env = {"GMVIBE_DB": str(tmp_path / "cli.db"), "PATH": "/usr/bin:/bin"}
    env.update({k: v for k, v in __import__("os").environ.items() if k in ("GMVIBE_STOCKFISH",)})
    result = subprocess.run(
        [sys.executable, "-m", "gmvibe", "analyze", str(pgn), "--depth", "8", "--no-deep"],
        cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert "win%" in result.stdout
    assert "cache hit" in result.stdout


def test_cli_missing_file_clean_error(tmp_path):
    """File assente: messaggio chiaro ed exit 2, non un traceback."""
    r = subprocess.run(
        [sys.executable, "-m", "gmvibe", "analyze", str(tmp_path / "nope.pgn")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
    assert "impossibile leggere" in r.stderr


def test_cli_empty_pgn_clean_error(tmp_path):
    """PGN senza partite: messaggio chiaro ed exit 2, non un traceback."""
    f = tmp_path / "empty.pgn"
    f.write_text("", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "gmvibe", "analyze", str(f)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
    assert "nessuna partita" in r.stderr.lower()
