"""Opening Explorer di Lichess (documentazione.txt §5.L).

Serve a sapere DOVE la partita esce dalla teoria e con quali statistiche reali. Richiede
rete: se non c'e', ritorna ``None`` e la funzione si disattiva (mai inventare statistiche).

Endpoint:
- master:  https://explorer.lichess.ovh/masters?fen=<fen>
- lichess: https://explorer.lichess.ovh/lichess?fen=<fen>

NOTA IMPORTANTE: a oggi l'endpoint richiede autenticazione (senza token risponde 401).
Serve un token Lichess personale, passato come ``Authorization: Bearer``. Senza token la
funzione si disattiva e ritorna ``None`` — l'uscita dalla teoria resta calcolata dal
dataset apertura (offline, deterministico).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

import chess

from ..models import OpeningExplorerConfig

_MASTERS = "https://explorer.lichess.ovh/masters"
_LICHESS = "https://explorer.lichess.ovh/lichess"


def _fetch(url: str, params: dict[str, str], token: str | None,
           timeout: float = 10.0) -> dict | None:
    query = urllib.parse.urlencode(params)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(f"{url}?{query}", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


class OpeningExplorer:
    """Interroga l'Opening Explorer e individua l'uscita dalla teoria."""

    def __init__(self, config: OpeningExplorerConfig | None = None, token: str | None = None) -> None:
        self.config = config or OpeningExplorerConfig()
        self.token = token

    def query(self, board: chess.Board) -> dict | None:
        """Statistiche per la posizione (masters + lichess), o None se non disponibile."""
        if not self.config.enabled or not self.config.allow_network:
            return None
        fen = board.fen()
        merged: dict = {"white": 0, "draws": 0, "black": 0, "moves": [], "sources": []}
        found = False
        if self.config.use_masters:
            data = _fetch(_MASTERS, {"fen": fen}, self.token)
            if data is not None:
                found = True
                merged["white"] += data.get("white", 0)
                merged["draws"] += data.get("draws", 0)
                merged["black"] += data.get("black", 0)
                merged["moves"].extend(data.get("moves", []))
                merged["sources"].append("masters")
        if self.config.use_lichess:
            data = _fetch(_LICHESS, {"fen": fen}, self.token)
            if data is not None:
                found = True
                merged["white"] += data.get("white", 0)
                merged["draws"] += data.get("draws", 0)
                merged["black"] += data.get("black", 0)
                merged["moves"].extend(data.get("moves", []))
                merged["sources"].append("lichess")
        return merged if found else None

    def total_games(self, stats: dict | None) -> int:
        if not stats:
            return 0
        return int(stats.get("white", 0) + stats.get("draws", 0) + stats.get("black", 0))

    def move_popularity(self, stats: dict | None, uci: str) -> int:
        """Numero di partite che hanno giocato ``uci`` dalla posizione."""
        if not stats:
            return 0
        total = 0
        for mv in stats.get("moves", []):
            if mv.get("uci") == uci:
                total += int(mv.get("white", 0) + mv.get("draws", 0) + mv.get("black", 0))
        return total

    def in_theory(self, stats: dict | None, uci: str) -> bool:
        """Vero se la posizione ha teoria sufficiente e la mossa giocata e' nota."""
        if not stats:
            return False
        if self.total_games(stats) < self.config.min_games:
            return False
        return self.move_popularity(stats, uci) > 0
