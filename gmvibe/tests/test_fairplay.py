"""FASE 8 — accettazione §8.3: nessuna analisi durante una partita live.

Criterio (§10): *non e' possibile in alcun modo chiamare l'analisi durante una partita
live*. Questi test lo verificano su **tutti** i percorsi: funnel del motore, layer LLM,
endpoint web e comando CLI. Offline: motore finto, nessuna rete.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gmvibe import fairplay
from gmvibe.analysis.pipeline import analyze_game
from gmvibe.fairplay import FairPlayViolation, gate
from gmvibe.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_gate():
    """Ogni test parte con la guardia abbassata (stato globale)."""
    fairplay.reset_gate()
    yield
    fairplay.reset_gate()


@pytest.fixture
def client(tmp_db, fake_runner):
    app = create_app(db=tmp_db, runner=fake_runner)
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Il gate in sé
# --------------------------------------------------------------------------- #
def test_gate_idle_by_default():
    assert gate().active is False
    gate().require_idle()  # non solleva
    assert gate().as_dict() == {"game_in_progress": False, "game_id": None}


def test_gate_blocks_then_releases():
    gate().begin("g1")
    assert gate().active is True and gate().game_id == "g1"
    with pytest.raises(FairPlayViolation):
        gate().require_idle()
    gate().end("g1")
    assert gate().active is False


def test_gate_end_only_matching_game():
    """La fine di un'altra partita non deve sbloccare quella in corso."""
    gate().begin("g1")
    gate().end("g2")            # id diverso: ignorato
    assert gate().active is True
    gate().end("g1")
    assert gate().active is False


# --------------------------------------------------------------------------- #
# Funnel: motore e LLM non partono mentre si gioca
# --------------------------------------------------------------------------- #
def test_engine_analysis_blocked_during_live(tmp_db, fake_runner, sample_short_text):
    gate().begin("g1")
    with pytest.raises(FairPlayViolation):
        analyze_game(sample_short_text, deep=False, db=tmp_db, runner=fake_runner)
    # nessuna chiamata al motore è stata fatta
    assert fake_runner.calls == 0


def test_engine_analysis_ok_after_game(tmp_db, fake_runner, sample_short_text):
    gate().begin("g1")
    gate().end("g1")
    analysis, _ = analyze_game(sample_short_text, deep=False, db=tmp_db, runner=fake_runner)
    assert analysis.rows


def test_coach_analysis_blocked_during_live():
    from gmvibe.ai_coach.coach import coach_analysis
    from gmvibe.models import GameAnalysis

    gate().begin("g1")
    with pytest.raises(FairPlayViolation):
        coach_analysis(GameAnalysis(rows=[], result="*"))


# --------------------------------------------------------------------------- #
# Web: gli endpoint di analisi rispondono 409
# --------------------------------------------------------------------------- #
def test_web_analyze_blocked_during_live(client, sample_short_text):
    gate().begin("g1")
    r = client.post("/api/analyze", json={"pgn": sample_short_text, "deep": False})
    assert r.status_code == 409
    assert "g1" in r.json()["detail"]


def test_web_coach_blocked_during_live(client):
    gate().begin("g1")
    r = client.post("/api/games/1/coach")
    assert r.status_code == 409


def test_web_analyze_works_when_idle(client, sample_short_text):
    r = client.post("/api/analyze", json={"pgn": sample_short_text, "deep": False})
    assert r.status_code == 202
    # attende il job per non chiudere il DB mentre gira
    import time

    jid = r.json()["job_id"]
    for _ in range(150):
        if client.get(f"/api/jobs/{jid}").json()["status"] in ("done", "error"):
            break
        time.sleep(0.02)


def test_live_state_reports_gate(client):
    assert client.get("/api/live/state").json()["gate"]["game_in_progress"] is False
    gate().begin("g1")
    body = client.get("/api/live/state").json()
    assert body["gate"] == {"game_in_progress": True, "game_id": "g1"}


# --------------------------------------------------------------------------- #
# CLI: analyze e import escono con errore mentre si gioca
# --------------------------------------------------------------------------- #
def _analyze_args(pgn_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        pgn=str(pgn_path), profile=None, depth=None, time=None, deep_depth=None,
        no_deep=True, refresh=False, save=False, coach=False, maia=False,
    )


def test_cli_analyze_blocked_during_live(capsys):
    from gmvibe.cli import analyze as analyze_cmd

    gate().begin("g1")
    rc = analyze_cmd.run(_analyze_args(FIXTURES / "sample_short.pgn"))
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "live" in err and "vietata" in err


def test_cli_import_blocked_during_live(capsys):
    from gmvibe.cli import import_cmd

    gate().begin("g1")
    args = argparse.Namespace(profile=None, lichess=None, chesscom=None, pgn=None)
    rc = import_cmd.run(args)
    assert rc == 2


def test_cli_analyze_help_lists_maia():
    r = subprocess.run([sys.executable, "-m", "gmvibe", "analyze", "--help"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "--maia" in r.stdout
