"""Client della Board API di Lichess (§8.2).

Autenticazione con **Personal Access Token** (scope ``board:play``) preso dai segreti del
profilo — mai una password, mai il token in chiaro nei log (§4.4). Le chiamate usano
``urllib`` come ``sources/lichess_import.py``: nessuna dipendenza aggiuntiva.

Endpoint usati (base ``https://lichess.org``):
- ``GET  /api/account``                          — il nostro username (per il lato/colore)
- ``POST /api/board/seek``                       — cerca partita (tempo, incremento, rated)
- ``GET  /api/stream/event``                     — stream NDJSON eventi (gameStart/Finish…)
- ``GET  /api/board/game/stream/{id}``           — stream NDJSON della partita
- ``POST /api/board/game/{id}/move/{uci}``       — invia una mossa
- ``POST /api/board/game/{id}/resign|abort``     — resa / abbandono
- ``POST /api/board/game/{id}/draw/{yes|no}``    — proposta di patta

Limite noto (§8.2): la Board API **non supporta il bullet**; blitz/rapid/classica sì. In
caso di **429** (rate limit) si attende e non si martella.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

__all__ = ["BoardAPI", "LichessLiveError", "iter_ndjson", "parse_event_line"]

_BASE = "https://lichess.org"
_USER_AGENT = "gmvibe/0.1 (+https://github.com/gmvibe)"
_MAX_RETRIES_429 = 3


class LichessLiveError(Exception):
    """Errore nel gioco live (token mancante, HTTP, rete, rate limit, ...)."""


def parse_event_line(line: str) -> dict[str, Any] | None:
    """Converte una riga NDJSON in dict; ``None`` per righe vuote/non JSON.

    Isolata per testare la tolleranza a keep-alive vuoti e a righe spazzatura senza
    toccare la rete.
    """
    line = (line or "").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def iter_ndjson(stream: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Itera gli eventi di uno stream NDJSON, saltando righe vuote/non valide."""
    for raw in stream:
        event = parse_event_line(raw)
        if event is not None:
            yield event


class BoardAPI:
    """Client sincrono della Board API. Usare con un token ``board:play``."""

    def __init__(self, token: str | None, *, timeout: float = 20.0, base: str = _BASE) -> None:
        if not token:
            raise LichessLiveError(
                "token Lichess mancante: il gioco live richiede un Personal Access Token "
                "con scope 'board:play' (profiles/<nick>/secrets.env → LICHESS_TOKEN)."
            )
        self._token = token
        self._timeout = timeout
        self._base = base.rstrip("/")
        # Iniettabile nei test: (Request, timeout) -> risposta file-like.
        self._urlopen = urllib.request.urlopen

    # ------------------------------------------------------------------ #
    # Trasporto
    # ------------------------------------------------------------------ #
    def _request(
        self, method: str, path: str, *, data: dict[str, str] | None = None, stream: bool = False
    ):
        """Esegue una richiesta autenticata; ritorna la risposta (file-like se ``stream``).

        Ritenta con attesa sui **429** (rate limit §8.2) invece di martellare.
        """
        url = f"{self._base}{path}"
        body = urllib.parse.urlencode(data).encode() if data else None
        headers = {"Authorization": f"Bearer {self._token}", "User-Agent": _USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES_429):
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                resp = self._urlopen(req, timeout=self._timeout)
                if stream:
                    return resp
                with resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < _MAX_RETRIES_429 - 1:
                    retry_after = int(exc.headers.get("Retry-After", "1") or "1")
                    time.sleep(min(max(retry_after, 1), 10))
                    last_exc = exc
                    continue
                if exc.code == 401:
                    raise LichessLiveError(
                        "token Lichess rifiutato (401): serve lo scope 'board:play'."
                    ) from exc
                raise LichessLiveError(f"{method} {path} → HTTP {exc.code}") from exc
            except Exception as exc:  # rete/timeout
                raise LichessLiveError(f"{method} {path} fallita: {exc}") from exc
        raise LichessLiveError(f"{method} {path}: rate limit persistente (429)") from last_exc

    # ------------------------------------------------------------------ #
    # Account e ricerca
    # ------------------------------------------------------------------ #
    def account(self) -> dict[str, Any]:
        """Il nostro profilo Lichess (per ricavare username e id)."""
        raw = self._request("GET", "/api/account")
        return json.loads(raw.decode("utf-8"))

    def seek(self, *, minutes: float, increment: int, rated: bool = False) -> None:
        """Cerca una partita (§8.2). Blitz/rapid/classica; bullet non supportato."""
        self._request(
            "POST", "/api/board/seek",
            data={
                "rated": str(bool(rated)).lower(),
                "time": f"{float(minutes):g}",
                "increment": str(int(increment)),
            },
        )

    # ------------------------------------------------------------------ #
    # Stream
    # ------------------------------------------------------------------ #
    def stream_events(self) -> Iterator[dict[str, Any]]:
        """Stream NDJSON degli eventi account (``/api/stream/event``)."""
        resp = self._request("GET", "/api/stream/event", stream=True)
        return iter_ndjson(self._iter_lines(resp))

    def stream_game(self, game_id: str) -> Iterator[dict[str, Any]]:
        """Stream NDJSON di una partita (``/api/board/game/stream/{id}``)."""
        gid = urllib.parse.quote(game_id, safe="")
        resp = self._request("GET", f"/api/board/game/stream/{gid}", stream=True)
        return iter_ndjson(self._iter_lines(resp))

    @staticmethod
    def _iter_lines(resp) -> Iterator[str]:
        """Righe da una risposta in streaming, chiudendola alla fine."""
        try:
            for raw in resp:
                yield raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()

    # ------------------------------------------------------------------ #
    # Mosse e azioni in partita
    # ------------------------------------------------------------------ #
    def play_move(self, game_id: str, uci: str) -> bytes:
        gid = urllib.parse.quote(game_id, safe="")
        move = urllib.parse.quote(uci, safe="")
        return self._request("POST", f"/api/board/game/{gid}/move/{move}")

    def resign(self, game_id: str) -> bytes:
        return self._request("POST", f"/api/board/game/{urllib.parse.quote(game_id, safe='')}/resign")

    def abort(self, game_id: str) -> bytes:
        return self._request("POST", f"/api/board/game/{urllib.parse.quote(game_id, safe='')}/abort")

    def offer_draw(self, game_id: str, *, accept: bool = True) -> bytes:
        gid = urllib.parse.quote(game_id, safe="")
        return self._request("POST", f"/api/board/game/{gid}/draw/{'yes' if accept else 'no'}")
