"""Import delle partite da Lichess (documentazione.txt §8.1).

Usa l'API pubblica di export delle partite:
``GET https://lichess.org/api/games/user/<username>`` con ``clocks=true`` per i tempi e
``evals=false`` (le valutazioni le calcoliamo noi, §8.1). Le partite pubbliche NON
richiedono autenticazione: basta lo username (§4.4). Un token personale (scope
``board:play``) serve solo per le partite private e va passato come ``Authorization:
Bearer`` — mai password, mai token nei log.

HTTP con ``urllib`` come ``tablebase.py``/``opening_explorer.py``: nessuna dipendenza in
più. Errori e timeout diventano :class:`LichessImportError` con un messaggio chiaro.
"""

from __future__ import annotations

import urllib.parse
import urllib.request

__all__ = ["LichessImportError", "fetch_games"]

_API = "https://lichess.org/api/games/user/{username}"
_USER_AGENT = "gmvibe/0.1 (+https://github.com/gmvibe)"


class LichessImportError(Exception):
    """Errore nell'import da Lichess (rete, HTTP, username inesistente, ...)."""


def fetch_games(
    username: str,
    *,
    max_games: int = 20,
    since: int | None = None,
    until: int | None = None,
    clocks: bool = True,
    opening: bool = True,
    evals: bool = False,
    token: str | None = None,
    timeout: float = 20.0,
) -> str:
    """Scarica le ultime partite di ``username`` come **PGN multi-partita**.

    Args:
        username: nickname Lichess (partite pubbliche, nessun token richiesto).
        max_games: numero massimo di partite (le più recenti).
        since/until: limiti temporali in **millisecondi** epoch (opzionali).
        clocks: include i tempi ``[%clk]`` (servono all'analisi del clock §5.J).
        opening: include il nome dell'apertura.
        evals: valuta le mosse (NO: le calcoliamo noi).
        token: token personale opzionale, solo per partite private.
        timeout: secondi massimi di attesa.

    Raises:
        LichessImportError: username vuoto, errore HTTP/rete o timeout.
    """
    username = (username or "").strip()
    if not username:
        raise LichessImportError("username Lichess mancante")

    params: dict[str, str] = {
        "max": str(max(1, int(max_games))),
        "clocks": str(bool(clocks)).lower(),
        "opening": str(bool(opening)).lower(),
        "evals": str(bool(evals)).lower(),
        "moves": "true",
        "pgnInJson": "false",
        "sort": "dateDesc",
    }
    if since is not None:
        params["since"] = str(int(since))
    if until is not None:
        params["until"] = str(int(until))

    url = f"{_API.format(username=urllib.parse.quote(username, safe=''))}?" + urllib.parse.urlencode(params)
    headers = {
        "Accept": "application/x-chess-pgn",  # PGN, non NDJSON
        "User-Agent": _USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except LichessImportError:  # pragma: no cover - difensivo
        raise
    except Exception as exc:  # urllib.error.HTTPError/URLError, timeout, ...
        raise LichessImportError(f"import da Lichess fallito per '{username}': {exc}") from exc
