"""Test della pipeline completo: accuratezza, fasi, Complexity (documentazione.txt §10 FASE 2)."""

from __future__ import annotations

from gmvibe.analysis.pipeline import analyze_game


def test_pipeline_populates_fase2_fields(tmp_db, fake_runner, sample_short_text):
    analysis, stats = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    assert analysis.accuracy.bianco is not None
    assert analysis.accuracy.nero is not None
    assert 0.0 <= analysis.accuracy.bianco <= 100.0
    # ogni riga ha fase e label
    for row in analysis.rows:
        assert row.phase in ("apertura", "mediogioco", "finale")
        assert row.label in ("ok", "imprecisione", "errore", "svarione")


def test_deep_pass_runs_on_candidates(tmp_db, fake_runner, sample_short_text):
    analysis, stats = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    candidates = [r for r in analysis.rows if r.is_candidate]
    assert candidates, "attesi candidati per il pass profondo"
    for row in candidates:
        assert row.deep_lines  # linee MultiPV salvate
        assert row.complexity is not None
        assert row.best_move_uci is not None


def test_deep_disabled(tmp_db, fake_runner, sample_short_text):
    analysis, stats = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep=False)
    assert stats["deep_analyses"] == 0
    assert all(not r.is_candidate for r in analysis.rows)


def test_accuracy_by_phase_present(tmp_db, fake_runner, sample_short_text):
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    assert analysis.accuracy_by_phase  # almeno una fase
    for phase, acc in analysis.accuracy_by_phase.items():
        assert phase in ("apertura", "mediogioco", "finale")


def test_counts_present(tmp_db, fake_runner, sample_short_text):
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    assert set(analysis.counts.keys()) == {"white", "black"}
    for c in analysis.counts.values():
        assert set(c.keys()) == {"imprecisione", "errore", "svarione"}


def test_every_error_has_a_label(tmp_db, fake_runner, sample_short_text):
    """Criterio §10 FASE 3: ogni errore ha un'etichetta."""
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    for row in analysis.rows:
        if row.label != "ok":
            assert row.error_label is not None, f"errore senza etichetta: {row.san}"
        else:
            assert row.error_label is None


def test_features_attached_to_errors(tmp_db, fake_runner, sample_short_text):
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    for row in analysis.rows:
        if row.label != "ok":
            assert row.features_before is not None
            assert row.features_delta


def test_opening_info_book_exit(tmp_db, fake_runner, sample_short_text):
    """L'uscita dalla teoria e' deterministica dal dataset apertura (§5.A)."""
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    assert analysis.opening is not None
    assert analysis.opening.name is not None
    # il campione segue una linea nominata poi devia -> exit_ply valorizzato
    assert analysis.opening.exit_ply is not None


def test_time_summary_present(tmp_db, fake_runner, sample_short_text):
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    assert analysis.time is not None
    assert analysis.time.cadenza in ("blitz", "rapid", "classica")
    assert analysis.time.white_avg is not None


def test_endgame_fixture_all_finale(tmp_db, fake_runner, sample_endgame_text):
    """Partita che inizia da un finale: tutte le mosse in fase 'finale', nessun crash."""
    analysis, stats = analyze_game(sample_endgame_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    assert analysis.rows
    assert all(r.phase == "finale" for r in analysis.rows)
    assert stats["moves"] == len(analysis.rows)


def test_reproducible(tmp_db, fake_runner, sample_short_text):
    a1, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    a2, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, deep_depth=10)
    assert [r.label for r in a1.rows] == [r.label for r in a2.rows]
    assert [r.phase for r in a1.rows] == [r.phase for r in a2.rows]
