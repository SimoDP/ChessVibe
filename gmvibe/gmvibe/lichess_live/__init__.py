"""Gioco live su Lichess (§8.2-8.3, FASE 8).

Due pezzi:
- :mod:`~gmvibe.lichess_live.board_api`: client HTTP della **Board API** (token personale
  con scope ``board:play``), con gli stream NDJSON di eventi e partita.
- :mod:`~gmvibe.lichess_live.session`: la macchina a stati che governa una partita e
  alza/abbassa la guardia fair play (``gmvibe.fairplay``) — **nessuna analisi mentre la
  partita e' in corso** (§8.3).

HTTP con ``urllib`` come il resto del progetto: nessuna dipendenza in piu' (il pacchetto
``berserk`` citato dalla spec non serve).
"""

from __future__ import annotations

from .board_api import BoardAPI, LichessLiveError, iter_ndjson, parse_event_line
from .session import LiveGameSession, fen_from_moves, reduce_event

__all__ = [
    "BoardAPI",
    "LichessLiveError",
    "LiveGameSession",
    "fen_from_moves",
    "iter_ndjson",
    "parse_event_line",
    "reduce_event",
]
