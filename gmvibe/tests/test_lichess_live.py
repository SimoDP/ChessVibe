"""Test del modulo di gioco live (§8.2-8.3), offline: nessuna rete, nessun token reale.

Il client Board API viene sostituito da un finto che pesca gli eventi da una coda; la
sessione (:class:`LiveGameSession`) e' quella vera, cosi' si verifica la macchina a stati
**e** la guardia fair play end-to-end (anche attraverso il server web).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gmvibe import fairplay
from gmvibe.lichess_live import (
    BoardAPI,
    LiveGameSession,
    LichessLiveError,
    fen_from_moves,
    iter_ndjson,
    parse_event_line,
    reduce_event,
)
from gmvibe.models import LiveGameState
from gmvibe.web.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_gate():
    fairplay.reset_gate()
    yield
    fairplay.reset_gate()


# --------------------------------------------------------------------------- #
# Parsing (puro)
# --------------------------------------------------------------------------- #
def test_parse_event_line():
    assert parse_event_line('{"type":"gameStart"}') == {"type": "gameStart"}
    assert parse_event_line("") is None
    assert parse_event_line("   ") is None
    assert parse_event_line("non json") is None
    assert parse_event_line("[1,2]") is None       # non e' un oggetto


def test_iter_ndjson_skips_noise():
    events = list(iter_ndjson(['{"type":"a"}', "", "garbage", '{"type":"b"}']))
    assert events == [{"type": "a"}, {"type": "b"}]


def test_fen_from_moves():
    assert fen_from_moves(None, ["e2e4", "e7e5"]) == (
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    )
    # mossa illegale: si ferma senza sollevare
    assert fen_from_moves(None, ["e2e4", "z9z9"]) == fen_from_moves(None, ["e2e4"])


# --------------------------------------------------------------------------- #
# Riduzione degli eventi (pura)
# --------------------------------------------------------------------------- #
def test_reduce_gamestart_and_finish():
    s = LiveGameState(me="me")
    s = reduce_event(s, {"type": "gameStart", "game": {
        "gameId": "abc", "color": "white", "rated": True,
        "opponent": {"username": "avv"}}}, me="me")
    assert s.status == "playing" and s.game_in_progress is True
    assert s.game_id == "abc" and s.my_color == "white" and s.opponent == "avv" and s.rated

    s = reduce_event(s, {"type": "gameFinish", "winner": "white"}, me="me")
    assert s.status == "finished" and s.game_in_progress is False and s.winner == "white"


def test_reduce_gamefull_and_gamestate():
    s = LiveGameState(me="me")
    s = reduce_event(s, {"type": "gameFull", "id": "abc", "rated": False,
                         "white": {"name": "me"}, "black": {"name": "avv"},
                         "initialFen": "startpos",
                         "state": {"moves": "e2e4 e7e5", "wtime": 300000, "btime": 295000}},
                    me="me")
    assert s.my_color == "white" and s.opponent == "avv"
    assert s.moves == ["e2e4", "e7e5"] and s.last_move == "e7e5"
    assert s.clock_white == 300.0 and s.clock_black == 295.0
    assert s.fen == fen_from_moves(None, ["e2e4", "e7e5"])

    s = reduce_event(s, {"type": "gameState", "moves": "e2e4 e7e5 g1f3",
                         "wtime": 290000, "btime": 290000, "status": "started"}, me="me")
    assert s.moves[-1] == "g1f3" and s.last_move == "g1f3" and s.game_in_progress is True

    s = reduce_event(s, {"type": "gameState", "moves": "e2e4", "status": "resign",
                         "winner": "black"}, me="me")
    assert s.status == "finished" and s.game_in_progress is False and s.winner == "black"


# --------------------------------------------------------------------------- #
# Board API: token obbligatorio, chiamate HTTP (trasporto finto)
# --------------------------------------------------------------------------- #
def test_board_api_requires_token():
    with pytest.raises(LichessLiveError):
        BoardAPI(None)
    with pytest.raises(LichessLiveError):
        BoardAPI("")


class _FakeResp:
    def __init__(self, data: bytes = b"{}") -> None:
        self._data = data
        self.closed = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._data.splitlines(keepends=True))


def test_board_api_account_and_seek():
    calls: list = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.method, req.full_url, req.data))
        if req.full_url.endswith("/api/account"):
            return _FakeResp(json.dumps({"username": "simo"}).encode())
        return _FakeResp(b"")

    api = BoardAPI("tok")
    api._urlopen = fake_urlopen
    assert api.account()["username"] == "simo"
    api.seek(minutes=5, increment=3, rated=True)
    method, url, data = calls[-1]
    assert method == "POST" and url.endswith("/api/board/seek")
    assert b"rated=true" in data and b"increment=3" in data
    # il token va in Authorization, mai nel corpo
    assert b"tok" not in (data or b"")


def test_board_api_stream_is_ndjson():
    payload = b'{"type":"gameStart"}\n\n{"type":"gameFinish"}\n'

    def fake_urlopen(req, timeout=None):
        return _FakeResp(payload)

    api = BoardAPI("tok")
    api._urlopen = fake_urlopen
    assert [e["type"] for e in api.stream_events()] == ["gameStart", "gameFinish"]


# --------------------------------------------------------------------------- #
# Sessione: guardia fair play end-to-end
# --------------------------------------------------------------------------- #
class FakeBoardAPI:
    """Board API finta: gli eventi arrivano da una coda, le azioni sono registrate."""

    def __init__(self, username: str = "me") -> None:
        self.username = username
        self.events: "queue.Queue" = queue.Queue()
        self.actions: list = []
        self.seek_kwargs: dict = {}

    def account(self):
        return {"username": self.username}

    def seek(self, *, minutes, increment, rated=False):
        self.seek_kwargs = {"minutes": minutes, "increment": increment, "rated": rated}

    def stream_events(self):
        while True:
            ev = self.events.get()
            if ev is None:
                return
            yield ev

    def stream_game(self, game_id):
        while True:
            ev = self.events.get()
            if ev is None:
                return
            yield ev

    def play_move(self, game_id, uci):
        self.actions.append(("move", game_id, uci))

    def resign(self, game_id):
        self.actions.append(("resign", game_id))

    def abort(self, game_id):
        self.actions.append(("abort", game_id))

    def offer_draw(self, game_id, *, accept=True):
        self.actions.append(("draw", game_id, accept))


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_session_gate_follows_events():
    api = FakeBoardAPI()
    session = LiveGameSession(api, me="me")
    session.handle_event({"type": "gameStart", "game": {"gameId": "abc", "color": "white"}})
    assert fairplay.gate().active is True
    session.handle_event({"type": "gameFinish", "winner": "white"})
    assert fairplay.gate().active is False


def test_session_delegates_actions():
    api = FakeBoardAPI()
    session = LiveGameSession(api, me="me")
    session.handle_event({"type": "gameStart", "game": {"gameId": "abc", "color": "black"}})
    session.play_move("e2e4")
    session.resign()
    session.offer_draw(accept=True)
    session.abort()
    assert ("move", "abc", "e2e4") in api.actions
    assert ("resign", "abc") in api.actions
    assert ("draw", "abc", True) in api.actions
    assert ("abort", "abc") in api.actions


def test_web_live_seek_then_analysis_blocked(tmp_db, fake_runner):
    """Accettazione via server: durante la partita l'analisi e' 409, poi torna 202."""
    api = FakeBoardAPI()
    app = create_app(db=tmp_db, runner=fake_runner, live_api=api)
    with TestClient(app) as c:
        r = c.post("/api/live/seek", json={"minutes": 5, "increment": 3})
        assert r.status_code == 202
        assert api.seek_kwargs == {"minutes": 5, "increment": 3, "rated": False}

        api.events.put({"type": "gameStart", "game": {"gameId": "abc", "color": "white"}})
        assert _wait(lambda: fairplay.gate().active)

        pgn = (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8")
        assert c.post("/api/analyze", json={"pgn": pgn, "deep": False}).status_code == 409

        api.events.put({"type": "gameFinish", "winner": "white"})
        assert _wait(lambda: not fairplay.gate().active)
        r2 = c.post("/api/analyze", json={"pgn": pgn, "deep": False})
        assert r2.status_code == 202
        # attende il job così il DB non viene chiuso mentre l'analisi gira
        assert _wait(lambda: c.get(f"/api/jobs/{r2.json()['job_id']}").json()["status"]
                     in ("done", "error"))


def test_web_seek_without_token_is_400(tmp_db, fake_runner):
    """Senza client iniettato e senza token, la ricerca spiega cosa manca (nessun crash)."""
    app = create_app(db=tmp_db, runner=fake_runner)  # live_api=None
    with TestClient(app) as c:
        r = c.post("/api/live/seek", json={"minutes": 5})
        assert r.status_code == 400
        assert "token" in r.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# Regressioni: guardia fair play non deve essere sbloccata per errore,
# FEN iniziale nelle partite non standard, gameState "started" prima di gameFull.
# --------------------------------------------------------------------------- #
def test_gate_end_none_does_not_clear_other_game():
    """end(None) non deve sbloccare una partita ALTRIMENTI in corso."""
    gate = fairplay.gate()
    gate.begin("gameY")
    gate.end(None)                 # es. una sessione che non ha mai avuto un game_id
    assert gate.active is True and gate.game_id == "gameY"
    gate.end("gameY")


def test_session_without_game_does_not_clear_foreign_gate():
    """Una sessione che termina senza partita NON deve sbloccare la guardia di un'altra
    partita in corso (bug fair-play: ricerca+stop sbloccava l'analisi)."""
    gate = fairplay.gate()
    gate.begin("gameY")                       # un'altra partita e' in corso
    api = FakeBoardAPI()
    session = LiveGameSession(api, me="me")
    session.start()                           # solo ricerca: nessun evento
    api.events.put(None)                      # lo stream finisce
    assert _wait(lambda: session.state.status in ("idle", "finished"))
    assert gate.active is True and gate.game_id == "gameY", "guardia sbloccata per errore"
    gate.end("gameY")


def test_gamestate_started_raises_gate_before_gamefull():
    """Un gameState 'started' puo' precedere gameFull: la partita e' comunque in corso."""
    s = reduce_event(LiveGameState(me="me"), {
        "type": "gameState", "status": "started", "moves": "e2e4",
        "wtime": 300000, "btime": 300000, "gameId": "g1",
    }, me="me")
    assert s.status == "playing" and s.game_in_progress is True


def test_gamestate_uses_initial_fen():
    """Le partite da posizione non standard devono ripartire dal loro FEN iniziale."""
    custom = "8/8/8/8/8/5k2/6q1/7K w - - 0 1"
    s = LiveGameState(me="me")
    s = reduce_event(s, {
        "type": "gameFull", "id": "g1", "rated": False,
        "white": {"name": "me"}, "black": {"name": "opp"},
        "initialFen": custom, "state": {"moves": "", "wtime": 300000, "btime": 300000},
    }, me="me")
    assert s.initial_fen == custom
    s2 = reduce_event(s, {"type": "gameState", "status": "started", "moves": "h1g1"},
                      me="me")
    # il FEN non torna allo startpos: e' la posizione custom + la mossa
    assert not s2.fen.startswith("rnbqkbnr/")


def test_gamestart_without_color_does_not_assume_white():
    s = reduce_event(LiveGameState(me="me"), {"type": "gameStart", "game": {"gameId": "g"}},
                     me="me")
    assert s.my_color is None


def test_web_double_seek_rejected(tmp_db, fake_runner):
    """Un secondo 'Cerca partita' mentre il primo cerca non deve creare due sessioni."""
    api = FakeBoardAPI()
    app = create_app(db=tmp_db, runner=fake_runner, live_api=api)
    with TestClient(app) as c:
        assert c.post("/api/live/seek", json={"minutes": 5}).status_code == 202
        r2 = c.post("/api/live/seek", json={"minutes": 3})
        assert r2.status_code == 409
        api.events.put(None)   # chiude lo stream per non lasciare thread appesi
