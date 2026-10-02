"""Config delle risorse del motore (§3) e factory del runner.

Il pass profondo a profondita' 22-26 su una singola thread e' il collo di bottiglia
dell'analisi: ``build_runner`` traduce ``engine.threads`` di config nel valore passato a
Stockfish. Nessun valore hardcoded (CLAUDE.md).
"""

from __future__ import annotations

import os

from gmvibe import config as cfg
from gmvibe.engine import stockfish_runner as sr
from gmvibe.models import EngineConfig


def test_resolve_threads():
    assert sr.resolve_threads(None) is None           # default del motore
    assert sr.resolve_threads(3) == 3
    assert sr.resolve_threads(0) == (os.cpu_count() or 1)   # 0 = tutti i core
    assert sr.resolve_threads(-1) == (os.cpu_count() or 1)


def test_build_runner_uses_engine_config(monkeypatch):
    captured: dict = {}

    class Dummy:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(sr, "StockfishRunner", Dummy)
    sr.build_runner(EngineConfig(threads=2, hash_mb=128))
    assert captured == {"threads": 2, "hash_mb": 128}

    captured.clear()
    sr.build_runner(EngineConfig(threads=0, hash_mb=0))
    assert captured == {"threads": os.cpu_count() or 1, "hash_mb": None}


def test_levels_yaml_has_engine_section():
    levels = cfg.load_levels()
    assert levels.engine.threads == 0
    assert levels.engine.hash_mb == 256

def test_deep_pass_is_time_bounded_by_default():
    """Il pass profondo usa il modo a tempo (prevedibile): depth null + time 5-10s."""
    levels = cfg.load_levels()
    assert levels.deep_pass.depth is None
    assert 5.0 <= levels.deep_pass.time <= 10.0
    assert levels.deep_pass.max_candidates == 16
