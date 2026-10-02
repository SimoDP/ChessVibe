"""Test del dataset apertura (ECO) e dell'Opening Explorer (documentazione.txt §5.A, §5.L)."""

from __future__ import annotations

import chess
import pytest

from gmvibe.sources.opening_explorer import OpeningExplorer
from gmvibe.sources.openings import load_opening_book
from gmvibe.models import OpeningExplorerConfig


def _book(tmp_path=None):
    # offline e deterministico: usa il fallback incluso nel pacchetto. Per essere
    # ermetico il cache_dir viene puntato a una cartella vuota (altrimenti userebbe il
    # dataset scaricato, che e' molto piu' grande).
    import pytest
    from gmvibe.sources import openings as _o

    if tmp_path is not None:
        mp = pytest.MonkeyPatch()
        mp.setattr(_o.paths, "openings_cache_dir", lambda: tmp_path / "nope")
        book = load_opening_book(allow_download=False)
        mp.undo()
        return book
    return load_opening_book(allow_download=False)


def test_eco_and_name(tmp_path):
    book = _book(tmp_path)
    eco, name, matched = book.match_full(["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"])
    assert name == "Ruy Lopez"
    assert eco == "C60"
    assert matched == 5


def test_eco_for_uci(tmp_path):
    book = _book(tmp_path)
    assert book.eco_for_uci(["e2e4", "c7c5"]) == "B20"


def test_in_book_exact(tmp_path):
    book = _book(tmp_path)
    assert book.in_book(["e2e4", "e7e5"])
    # sequenza non esattamente nel dataset (fallback) -> fuori teoria
    assert not book.in_book(["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6"])


# --------------------------------------------------------------------------- #
# Opening Explorer (rete)
# --------------------------------------------------------------------------- #
def _explorer_online() -> bool:
    return OpeningExplorer(OpeningExplorerConfig()).query(chess.Board()) is not None


@pytest.mark.skipif(not _explorer_online(), reason="Opening Explorer non raggiungibile")
def test_explorer_start_position():
    ex = OpeningExplorer(OpeningExplorerConfig())
    stats = ex.query(chess.Board())
    assert stats is not None
    assert ex.total_games(stats) > 0
    assert ex.in_theory(stats, "e2e4") is True


def test_explorer_offline_returns_none():
    ex = OpeningExplorer(OpeningExplorerConfig(allow_network=False))
    assert ex.query(chess.Board()) is None
