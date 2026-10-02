"""Test del server web e della UI di review (documentazione.txt §9, §10 FASE 6).

Girano **offline**: il motore è iniettato (:class:`FakeRunner`) e il client LLM è un
finto client duck-typed. Nessuna rete, nessuna chiave API, nessun Stockfish reale.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import chess
import pytest
from fastapi.testclient import TestClient

from gmvibe.ai_coach.anthropic_client import LLMResponse
from gmvibe.models import LLMUsage
from gmvibe.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


class FakeCoachClient:
    """Client LLM finto: genera un report ancorato ai momenti del contesto."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_structured(self, *, system, messages, schema, model, max_tokens, cache):
        self.calls += 1
        user = messages[0]["content"]
        marker = "PACCHETTO DATI (JSON):"
        ctx = json.loads(user.split(marker, 1)[1] if marker in user else user)
        momenti = []
        for m in ctx.get("momenti_critici", []):
            board = chess.Board(m["fen_prima"])
            uci = (m.get("mossa_migliore_motore") or {}).get("uci") or m["mossa_giocata_uci"]
            san = board.san(chess.Move.from_uci(uci))
            momenti.append({
                "id": m["id"], "fen_prima": m["fen_prima"],
                "mossa_giocata": m["mossa_giocata"], "mossa_migliore": san,
                "titolo": "t", "tipo_errore": m.get("etichetta", ""), "spiegazione": "s",
                "idea_del_motore": "i", "regola_di_pensiero": "r",
                "varianti_citate": [{"fen_partenza": m["fen_prima"], "mosse_san": [san], "commento": "c"}],
            })
        payload = {
            "sintesi_partita": "Sintesi del Maestro.", "apertura": {"valutazione": "ok"},
            "momenti": momenti, "gestione_tempo": "gt", "fase_piu_debole": "mediogioco",
            "lezione_finale": "lf", "da_allenare": ["a", "b", "c"],
        }
        return LLMResponse(text=json.dumps(payload), model=model,
                           usage=LLMUsage(model=model, input_tokens=1000, output_tokens=200,
                                          cost_usd=0.02))


@pytest.fixture
def client(tmp_db, fake_runner):
    app = create_app(db=tmp_db, runner=fake_runner, coach_client=FakeCoachClient())
    with TestClient(app) as c:
        yield c


def _wait(client: TestClient, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = client.get(f"/api/jobs/{job_id}").json()
        if st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} non terminato entro {timeout}s")


def _analyze(client: TestClient, pgn: str, **extra) -> int:
    r = client.post("/api/analyze", json={"pgn": pgn, "deep": False, **extra})
    assert r.status_code == 202, r.text
    st = _wait(client, r.json()["job_id"])
    assert st["status"] == "done", st
    return st["game_id"]


# --------------------------------------------------------------------------- #
# Pagina e statici
# --------------------------------------------------------------------------- #
def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "chessground" in r.text.lower()
    assert "/static/app.js" in r.text


def test_static_assets_served(client):
    for path in (
        "/static/style.css", "/static/app.js",
        "/static/vendor/chessground.min.js", "/static/vendor/chessground.base.css",
        "/static/vendor/chessground.brown.css", "/static/vendor/chessground.cburnett.css",
        "/static/vendor/chess.js",
    ):
        assert client.get(path).status_code == 200, path


def test_vendor_is_local_not_cdn(client):
    """Gli asset devono essere vendorizzati: nessun riferimento a CDN nell'HTML."""
    html = client.get("/").text
    assert "cdn.jsdelivr" not in html and "unpkg.com" not in html


# --------------------------------------------------------------------------- #
# Analisi
# --------------------------------------------------------------------------- #
def test_profiles_endpoint(client):
    r = client.get("/api/profiles")
    assert r.status_code == 200
    assert "profiles" in r.json()


def test_analyze_and_fetch_game(client):
    gid = _analyze(client, (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8"))
    r = client.get(f"/api/games/{gid}")
    assert r.status_code == 200
    g = r.json()
    assert len(g["rows"]) > 0
    assert g["accuracy"]["bianco"] is not None
    assert g.get("opening") is not None
    assert "moments" in g and isinstance(g["moments"], list)
    # ogni riga ha i campi che la UI usa
    for row in g["rows"]:
        for key in ("ply", "san", "uci", "fen_before", "fen_after", "color", "winpct_after"):
            assert key in row


def test_analyze_empty_pgn_is_400(client):
    r = client.post("/api/analyze", json={"pgn": "   "})
    assert r.status_code == 400


def test_analyze_invalid_pgn_job_reports_error(client):
    r = client.post("/api/analyze", json={"pgn": "non un pgn 123 !!!", "deep": False})
    st = _wait(client, r.json()["job_id"])
    assert st["status"] == "error"
    assert st["error"]


def test_analysis_is_persisted(tmp_db, client):
    gid = _analyze(client, (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8"))
    row = tmp_db.get_analysis(gid)
    assert row is not None
    assert json.loads(row["analysis_json"])["rows"]


def test_unknown_game_is_404(client):
    assert client.get("/api/games/999999").status_code == 404
    assert client.get("/api/jobs/nonexistent").status_code == 404


def test_list_games(client):
    _analyze(client, (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8"))
    r = client.get("/api/games")
    assert r.status_code == 200
    assert len(r.json()["games"]) >= 1


# --------------------------------------------------------------------------- #
# Coach (§6) — job separato, degrado pulito
# --------------------------------------------------------------------------- #
def test_coach_job_with_fake_client(client):
    gid = _analyze(client, (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8"))
    r = client.post(f"/api/games/{gid}/coach")
    assert r.status_code == 202
    st = _wait(client, r.json()["job_id"])
    assert st["status"] == "done", st

    data = client.get(f"/api/games/{gid}/coach").json()
    assert data["report"]["sintesi_partita"] == "Sintesi del Maestro."
    assert data["meta"]["validated"] is True
    assert data["meta"]["varianti_legali_pct"] == 100.0
    assert data["meta"]["usage"]["cost_usd"] > 0


def test_coach_without_llm_client_is_graceful(tmp_db, fake_runner):
    """Senza client/chiave il job fallisce, ma l'analisi del motore resta disponibile."""
    app = create_app(db=tmp_db, runner=fake_runner, coach_client=None)
    with TestClient(app) as c:
        gid = _analyze(c, (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8"))
        st = _wait(c, c.post(f"/api/games/{gid}/coach").json()["job_id"])
        assert st["status"] == "error"
        assert st["error"]
        # il report del motore resta servibile
        assert c.get(f"/api/games/{gid}").status_code == 200
        # e il coach non è ancora disponibile
        assert c.get(f"/api/games/{gid}/coach").status_code == 404


def test_coach_before_generation_is_404(client):
    gid = _analyze(client, (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8"))
    assert client.get(f"/api/games/{gid}/coach").status_code == 404


# --------------------------------------------------------------------------- #
# Cross-partita (§7): profilo, puzzle, ripasso — e ricorrenza nel report
# --------------------------------------------------------------------------- #
def test_game_json_carries_recurrence_field(client):
    gid = _analyze(client, (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8"))
    g = client.get(f"/api/games/{gid}").json()
    # il campo esiste su ogni riga e ogni momento (default 0 alla prima partita)
    assert all("recurrence_games" in r for r in g["rows"])
    assert all("recurrence_games" in m for m in g["moments"])


def test_profile_endpoint(client):
    _analyze(client, (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8"))
    r = client.get("/api/profile")
    assert r.status_code == 200
    body = r.json()
    assert "label_frequency" in body and "accuracy_by_phase" in body
    assert body["games_analyzed"] >= 1
    assert "da_allenare" in body


def test_puzzles_endpoint_and_review(client, tmp_db):
    from gmvibe.models import Puzzle

    pid = tmp_db.save_puzzle(Puzzle(
        user_id="default", fen="r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1",
        solution_uci="f3g5", solution_san="Ng5", alternatives=["c4f7"],
        theme="tattica_mancata", explanation="test",
    ))
    r = client.get("/api/puzzles")
    assert r.status_code == 200
    puzzles = r.json()["puzzles"]
    assert len(puzzles) == 1
    p = puzzles[0]
    assert p["alternatives"] == ["c4f7"] and p["box"] == 1 and p["solution_san"] == "Ng5"
    # i dovuti includono il puzzle appena creato
    assert len(client.get("/api/puzzles?due=true").json()["puzzles"]) == 1
    # ripasso corretto → box 2
    r2 = client.post(f"/api/puzzles/{pid}/review", json={"correct": True})
    assert r2.status_code == 200
    assert r2.json()["box"] == 2 and r2.json()["due_at"]
    assert tmp_db.get_review(pid)["times_correct"] == 1


def test_review_unknown_puzzle_404(client):
    assert client.post("/api/puzzles/999999/review", json={"correct": True}).status_code == 404


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_serve_help_exits_zero():
    r = subprocess.run(
        [sys.executable, "-m", "gmvibe", "serve", "--help"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert "--port" in r.stdout
