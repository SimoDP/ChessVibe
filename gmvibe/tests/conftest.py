"""Fixture condivise dei test.

I test unitari non richiedono Stockfish: usano modelli e configurazioni reali, ma il
motore viene iniettato/finto. I test marcati ``slow`` usano il binario reale.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gmvibe import config as cfg
from gmvibe.db import Database
from gmvibe.sources.pgn_loader import parse_pgn_text

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def levels():
    return cfg.load_levels()


@pytest.fixture
def conversion():
    return cfg.load_rating_conversion()


@pytest.fixture
def tmp_db(tmp_path):
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def fixture_dir():
    return FIXTURES


@pytest.fixture
def sample_short_text():
    return (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8")


@pytest.fixture
def sample_draw_text():
    return (FIXTURES / "sample_draw.pgn").read_text(encoding="utf-8")


@pytest.fixture
def sample_noclk_text():
    return (FIXTURES / "sample_noclk.pgn").read_text(encoding="utf-8")


@pytest.fixture
def sample_endgame_text():
    return (FIXTURES / "sample_endgame.pgn").read_text(encoding="utf-8")


@pytest.fixture
def profile_1900():
    from gmvibe.models import PlayerProfile

    return PlayerProfile(nickname="t1900", rating=1900, rating_tipo="fide", cadenza_preferita="blitz")


@pytest.fixture
def profile_2200():
    from gmvibe.models import PlayerProfile

    return PlayerProfile(nickname="t2200", rating=2200, rating_tipo="fide", cadenza_preferita="blitz")


# --------------------------------------------------------------------------- #
# Runner finto: non avvia Stockfish, conta le chiamate e restituisce eval stabili.
# --------------------------------------------------------------------------- #
class FakeRunner:
    """Sostituto di StockfishRunner per i test unitari.

    Conta le chiamate al motore cosi' il test della cache puo' verificare che la seconda
    esecuzione NON interroghi Stockfish. Restituisce una valutazione deterministica
    basata sul numero di mosse legali (sufficiente per i test di flusso).
    """

    def __init__(self, version: str = "FakeEngine 1.0 (test)"):
        self._version = version
        self.calls = 0

    def engine_version_key(self) -> str:
        return self._version

    def analyse_position(self, board, *, depth=None, time=None, multipv=1, root_moves=None):
        from gmvibe.models import EngineEval, PVLine, WDL

        self.calls += 1
        cp = board.legal_moves.count()  # valore deterministico, non serve che sia sensato
        pv = []
        for mv in list(board.legal_moves)[:multipv]:
            pv.append(mv.uci())
        lines = [
            PVLine(rank=i + 1, move_uci=u, score_cp=cp, mate=None,
                   wdl=WDL(wins=500, draws=400, losses=100), pv_uci=[u])
            for i, u in enumerate(pv)
        ]
        turn = "white" if board.turn else "black"
        return EngineEval(
            fen=board.fen(), depth=depth or 0, multipv=multipv,
            engine_name="FakeEngine", engine_version=self._version, turn=turn, lines=lines,
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


@pytest.fixture
def fake_runner():
    return FakeRunner()


@pytest.fixture(autouse=True)
def _no_network_openings(monkeypatch, tmp_path):
    """Impedisce ai test di scaricare il dataset apertura: usa il fallback offline."""
    # Forza load_opening_book a non scaricare (rete assente in CI). Va patchato sia il
    # nome nel modulo sorgente sia quello importato in analyze (binding diretto).
    from gmvibe.sources import openings

    real = openings.load_opening_book

    def offline(*, allow_download=True):
        return real(allow_download=False)

    monkeypatch.setattr(openings, "load_opening_book", offline)
    monkeypatch.setattr("gmvibe.analysis.pipeline.load_opening_book", offline)

    # Isola la rete: l'Opening Explorer e la tablebase Lichess usano fakes, cosi' i test
    # non dipendono dalla rete ne' la interrogano.
    from gmvibe.sources import opening_explorer as oe
    from gmvibe.analysis import tablebase as tb

    class _OfflineExplorer:
        def __init__(self, *a, **k):
            pass

        def query(self, board):
            return None

        def in_theory(self, stats, uci):
            return False

    class _OfflineTablebase:
        def __init__(self, *a, **k):
            pass

        def probe(self, board):
            return None

    monkeypatch.setattr(oe, "OpeningExplorer", _OfflineExplorer)
    monkeypatch.setattr("gmvibe.analysis.pipeline.OpeningExplorer", _OfflineExplorer)
    monkeypatch.setattr("gmvibe.analysis.pipeline.TablebaseProbe", _OfflineTablebase)
    yield
