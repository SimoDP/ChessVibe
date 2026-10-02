"""Import delle partite da Chess.com (documentazione.txt §8.1).

Chess.com espone API **pubbliche in sola lettura** (§4.4: serve solo lo username):
- ``GET /pub/player/<u>/games/archives`` → elenco degli archivi mensili;
- ``GET /pub/player/<u>/games/<yyyy>/<mm>`` → le partite del mese, ognuna col campo ``pgn``.

Ogni chiamata richiede un ``User-Agent`` esplicito. Si prendono gli archivi più recenti
e, al loro interno, le partite più recenti, fino a ``max_games``.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

__all__ = ["ChessComImportError", "fetch_archives", "fetch_games"]

_ARCHIVES = "https://api.chess.com/pub/player/{username}/games/archives"
_USER_AGENT = "gmvibe/0.1 (+https://github.com/gmvibe)"


class ChessComImportError(Exception):
    """Errore nell'import da Chess.com (rete, HTTP, username inesistente, ...)."""


def _get_json(url: str, *, timeout: float) -> dict:
    headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        raise ChessComImportError(f"richiesta a Chess.com fallita ({url}): {exc}") from exc


def fetch_archives(username: str, *, timeout: float = 20.0) -> list[str]:
    """Elenco degli URL degli archivi mensili (dal più vecchio al più recente)."""
    username = (username or "").strip()
    if not username:
        raise ChessComImportError("username Chess.com mancante")
    url = _ARCHIVES.format(username=urllib.parse.quote(username, safe=""))
    data = _get_json(url, timeout=timeout)
    archives = data.get("archives") or []
    if not isinstance(archives, list):
        raise ChessComImportError(f"formato archivi inatteso per '{username}'")
    return [str(a) for a in archives]


def fetch_games(username: str, *, max_games: int = 20, timeout: float = 20.0) -> str:
    """Scarica le ultime partite di ``username`` come **PGN multi-partita**.

    Scorre gli archivi dal più recente e, dentro ciascuno, le partite dalla più recente,
    accumulando i ``pgn`` fino a ``max_games``.

    Raises:
        ChessComImportError: username vuoto, errore HTTP/rete o timeout.
    """
    if max_games <= 0:
        return ""
    archives = fetch_archives(username, timeout=timeout)
    blocks: list[str] = []
    count = 0
    # Dal mese più recente al più vecchio finché non raggiungiamo max_games.
    for archive_url in reversed(archives):
        data = _get_json(archive_url, timeout=timeout)
        games = data.get("games") or []
        if not isinstance(games, list):
            continue
        for game in reversed(games):
            pgn = (game or {}).get("pgn")
            if not pgn or not str(pgn).strip():
                continue  # partite senza PGN (es. daily in corso): si saltano
            blocks.append(str(pgn).strip())
            count += 1
            if count >= max_games:
                break
        if count >= max_games:
            break
    return "\n\n".join(blocks) + ("\n" if blocks else "")
