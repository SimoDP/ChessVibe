"""Test dell'import da Lichess/Chess.com e della divisione multi-PGN (§8.1, FASE 7).

Girano **offline**: ``urllib.request.urlopen`` è sostituito con una risposta finta che
legge dalle fixture. Nessuna rete, nessun token.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from gmvibe.sources import chesscom_import, lichess_import
from gmvibe.sources.pgn_loader import parse_pgn_text, split_pgn_games

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


def _patch_urlopen(monkeypatch, responses: dict[str, bytes], *, default_error=None):
    """Sostituisce urlopen: risponde in base al prefisso dell'URL richiesto."""
    calls: list[str] = []

    def fake(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        for prefix, body in responses.items():
            if url.startswith(prefix):
                return _FakeResponse(body)
        if default_error is not None:
            raise default_error
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


# --------------------------------------------------------------------------- #
# Multi-PGN
# --------------------------------------------------------------------------- #
def test_split_pgn_games_multi():
    text = (FIXTURES / "lichess_export.pgn").read_text(encoding="utf-8")
    games = split_pgn_games(text)
    assert len(games) == 2
    for chunk in games:
        assert parse_pgn_text(chunk).moves  # ogni pezzo è una partita valida


def test_split_pgn_single_still_works():
    text = (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8")
    assert len(split_pgn_games(text)) == 1


# --------------------------------------------------------------------------- #
# Lichess
# --------------------------------------------------------------------------- #
def test_lichess_fetch_games(monkeypatch):
    body = (FIXTURES / "lichess_export.pgn").read_bytes()
    calls = _patch_urlopen(monkeypatch, {"https://lichess.org/api/games/user/t1900": body})

    pgn = lichess_import.fetch_games("t1900", max_games=2)
    games = split_pgn_games(pgn)
    assert len(games) == 2
    assert parse_pgn_text(games[0]).white == "t1900"
    # i clock sono richiesti e presenti
    assert parse_pgn_text(games[0]).moves[0].clock_seconds == 300.0
    # parametri corretti nella query
    assert "clocks=true" in calls[0] and "evals=false" in calls[0] and "max=2" in calls[0]


def test_lichess_token_sent(monkeypatch):
    body = (FIXTURES / "lichess_export.pgn").read_bytes()
    captured = {}

    def fake(req, timeout=None):
        captured["auth"] = req.get_header("Authorization")
        return _FakeResponse(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    lichess_import.fetch_games("t1900", token="secret-token")
    assert captured["auth"] == "Bearer secret-token"


def test_lichess_empty_username_raises():
    with pytest.raises(lichess_import.LichessImportError):
        lichess_import.fetch_games("   ")


def test_lichess_network_error_raises(monkeypatch):
    _patch_urlopen(monkeypatch, {}, default_error=urllib.error.URLError("boom"))
    with pytest.raises(lichess_import.LichessImportError):
        lichess_import.fetch_games("t1900")


# --------------------------------------------------------------------------- #
# Chess.com
# --------------------------------------------------------------------------- #
def test_chesscom_fetch_games(monkeypatch):
    archive = (FIXTURES / "chesscom_archive.json").read_bytes()
    archives = json.dumps({
        "archives": ["https://api.chess.com/pub/player/t1900/games/2026/08"]
    }).encode()
    _patch_urlopen(monkeypatch, {
        "https://api.chess.com/pub/player/t1900/games/archives": archives,
        "https://api.chess.com/pub/player/t1900/games/2026/08": archive,
    })

    pgn = chesscom_import.fetch_games("t1900", max_games=5)
    games = split_pgn_games(pgn)
    # la terza voce non ha "pgn": viene saltata
    assert len(games) == 2
    assert parse_pgn_text(games[0]).white in ("t1900", "altro")


def test_chesscom_respects_max(monkeypatch):
    archive = (FIXTURES / "chesscom_archive.json").read_bytes()
    archives = json.dumps({
        "archives": ["https://api.chess.com/pub/player/t1900/games/2026/08"]
    }).encode()
    _patch_urlopen(monkeypatch, {
        "https://api.chess.com/pub/player/t1900/games/archives": archives,
        "https://api.chess.com/pub/player/t1900/games/2026/08": archive,
    })
    assert len(split_pgn_games(chesscom_import.fetch_games("t1900", max_games=1))) == 1


def test_chesscom_empty_username_raises():
    with pytest.raises(chesscom_import.ChessComImportError):
        chesscom_import.fetch_archives("  ")


def test_chesscom_network_error_raises(monkeypatch):
    _patch_urlopen(monkeypatch, {}, default_error=urllib.error.URLError("boom"))
    with pytest.raises(chesscom_import.ChessComImportError):
        chesscom_import.fetch_archives("t1900")
