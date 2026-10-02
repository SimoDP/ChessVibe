"""Test della cache SQLite del motore (documentazione.txt §5.D, §10)."""

from __future__ import annotations

from gmvibe.cli.analyze import analyze_game
from gmvibe.models import EngineEval, PVLine, WDL


def test_cache_roundtrip(tmp_db):
    ev = EngineEval(
        fen="8/8/8/8/4k3/8/4K3/8 w - - 0 1",
        depth=12, multipv=1, engine_name="Fake", engine_version="Fake 1.0",
        turn="white",
        lines=[PVLine(rank=1, move_uci="e2e3", score_cp=10, mate=None,
                      wdl=WDL(wins=500, draws=400, losses=100), pv_uci=["e2e3"])],
    )
    assert tmp_db.cache_get(ev.fen, 12, 1, "Fake 1.0") is None
    tmp_db.cache_put(ev)
    got = tmp_db.cache_get(ev.fen, 12, 1, "Fake 1.0")
    assert got is not None
    assert got.lines[0].move_uci == "e2e3"
    assert got.lines[0].wdl.wins == 500


def test_cache_key_includes_version(tmp_db):
    ev = EngineEval(fen="8/8/8/8/4k3/8/4K3/8 w - - 0 1", depth=12, multipv=1,
                    engine_name="Fake", engine_version="Fake 1.0", turn="white", lines=[])
    tmp_db.cache_put(ev)
    # versione diversa -> miss (upgrade motore invalida la cache)
    assert tmp_db.cache_get(ev.fen, 12, 1, "Fake 2.0") is None


def test_cache_key_includes_depth_multipv(tmp_db):
    ev = EngineEval(fen="8/8/8/8/4k3/8/4K3/8 w - - 0 1", depth=12, multipv=1,
                    engine_name="Fake", engine_version="v", turn="white", lines=[])
    tmp_db.cache_put(ev)
    assert tmp_db.cache_get(ev.fen, 14, 1, "v") is None
    assert tmp_db.cache_get(ev.fen, 12, 4, "v") is None


def test_second_run_uses_cache(tmp_db, fake_runner, sample_short_text):
    # 1a esecuzione: interroga il motore
    a1, s1 = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8)
    assert s1["engine_calls"] > 0
    calls_after_first = fake_runner.calls

    # 2a esecuzione: nessuna chiamata al motore (fast + deep sono tutti in cache)
    a2, s2 = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8)
    assert s2["engine_calls"] == 0
    assert s2["cache_hits"] >= s2["moves"]
    assert fake_runner.calls == calls_after_first

    # risultati coerenti
    assert len(a1.rows) == len(a2.rows)


def test_refresh_bypasses_cache(tmp_db, fake_runner, sample_short_text):
    analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8)
    calls = fake_runner.calls
    _, s = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8, refresh=True)
    assert s["engine_calls"] > 0
    assert fake_runner.calls > calls
