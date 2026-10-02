"""Sessione di gioco live: macchina a stati + guardia fair play (§8.2-8.3).

La sessione consuma lo stream eventi dell'account e, quando inizia una partita, ne segue
lo stream aggiornando :class:`~gmvibe.models.LiveGameState`. **Nel momento in cui la
partita inizia alza la guardia fair play** (``fairplay.gate().begin``) e la abbassa solo
alla fine: da quel momento ogni analisi (motore/LLM) e' vietata dal codice (§8.3).

La logica di riduzione degli eventi (:func:`reduce_event`) e' **pura**, cosi' i test la
esercitano senza rete ne' thread; l'orchestrazione con thread serve solo a CLI/web.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Iterable

import chess

from ..fairplay import FairPlayGate, gate as default_gate
from ..models import LiveGameState

__all__ = ["LiveGameSession", "fen_from_moves", "reduce_event"]


def fen_from_moves(initial_fen: str | None, moves: Iterable[str]) -> str:
    """FEN dopo una sequenza di mosse UCI (ignora le mosse non legali per robustezza)."""
    board = chess.Board(initial_fen) if initial_fen and initial_fen != "startpos" else chess.Board()
    for uci in moves:
        try:
            board.push_uci(uci)
        except (ValueError, chess.IllegalMoveError):
            break
    return board.fen()


def _side_of(name: str | None, me: str | None) -> str | None:
    return None if not me else ("white" if (name or "").lower() == me.lower() else "black")


def reduce_event(state: LiveGameState, event: dict[str, Any], *, me: str | None = None) -> LiveGameState:
    """Applica un evento dello stream a ``state`` (funzione pura, ritorna il nuovo stato)."""
    etype = event.get("type")
    if etype == "gameStart":
        game = event.get("game") or {}
        color = game.get("color")
        opponent = ((game.get("opponent") or {}).get("username")) if game.get("opponent") else None
        return state.model_copy(update={
            "status": "playing",
            "game_id": game.get("gameId"),
            # senza "color" non si puo' dedurre il lato: NON assumere "white"
            # (il fallback precedente _side_of(me, me) ritornava sempre "white").
            "my_color": color if color in ("white", "black") else state.my_color,
            "opponent": opponent,
            "rated": bool(game.get("rated", False)),
            "fen": game.get("fen") or state.fen,
            "game_in_progress": True,
            "result": None,
            "winner": None,
        })

    if etype == "gameFull":
        white = (event.get("white") or {}).get("name")
        black = (event.get("black") or {}).get("name")
        initial = event.get("initialFen")
        sub = event.get("state") or {}
        moves = _split_moves(sub.get("moves"))
        return state.model_copy(update={
            "status": "playing",
            "game_id": event.get("id") or state.game_id,
            "rated": bool(event.get("rated", state.rated)),
            "me": state.me or (white if (white or "").lower() == (me or "").lower() else None),
            "my_color": _side_of(white, me) if me else state.my_color,
            "opponent": black if (white or "").lower() == (me or "").lower() else white,
            "fen": fen_from_moves(initial, moves),
            "initial_fen": initial or state.initial_fen,
            "moves": moves,
            "last_move": moves[-1] if moves else None,
            "clock_white": _seconds(sub.get("wtime")),
            "clock_black": _seconds(sub.get("btime")),
            "game_in_progress": True,
        })

    if etype == "gameState":
        moves = _split_moves(event.get("moves"))
        # Riparte dalla FEN INIZIALE della partita (catturata in gameFull), non sempre
        # dallo startpos: le partite da posizione non standard (studi, handicap) devono
        # essere ricostruite dal loro FEN, altrimenti la scacchiera mostra la posizione
        # sbagliata.
        update: dict[str, Any] = {
            "fen": fen_from_moves(state.initial_fen, moves),
            "moves": moves,
            "last_move": moves[-1] if moves else None,
            "clock_white": _seconds(event.get("wtime")),
            "clock_black": _seconds(event.get("btime")),
        }
        status = event.get("status")
        if status in ("mate", "resign", "draw", "stalemate", "timeout", "aborted", "outoftime",
                      "cheat", "noStart", "unknownFinish", "variantEnd"):
            return _finish(state, event, update)
        # Un gameState "started" puo' arrivare PRIMA di gameStart/gameFull: la partita e'
        # in corso, quindi alza comunque il blocco fair play (§8.3).
        if status == "started" and not state.game_in_progress:
            update.update({"status": "playing", "game_in_progress": True,
                           "game_id": event.get("gameId") or state.game_id})
        return state.model_copy(update=update)

    if etype == "gameFinish":
        return _finish(state, event, {})

    return state


def _finish(state: LiveGameState, event: dict[str, Any], extra: dict[str, Any]) -> LiveGameState:
    """Marca la partita come finita (e quindi sblocca l'analisi, §8.3)."""
    winner = event.get("winner")
    return state.model_copy(update={
        "status": "finished",
        "game_in_progress": False,
        "winner": winner,
        "result": event.get("result") or state.result,
        **extra,
    })


def _split_moves(moves: str | None) -> list[str]:
    return [m for m in (moves or "").split() if m]


def _seconds(ms: int | None) -> float | None:
    return None if ms is None else round(int(ms) / 1000.0, 1)


class LiveGameSession:
    """Governa una partita live e la guardia fair play (§8.3).

    ``api`` e' duck-typed (basta :class:`~gmvibe.lichess_live.board_api.BoardAPI` o un
    finto nei test). ``on_update`` viene chiamato dopo ogni cambio di stato (per la UI).
    """

    def __init__(
        self,
        api: object,
        *,
        me: str | None = None,
        levels: Any | None = None,
        gate: FairPlayGate | None = None,
        on_update: Callable[[LiveGameState], None] | None = None,
    ) -> None:
        self._api = api
        self._gate = gate or default_gate()
        self._on_update = on_update
        self._levels = levels
        self._state = LiveGameState(me=me, status="idle")
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ #
    # Stato
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> LiveGameState:
        with self._lock:
            return self._state.model_copy()

    def _set(self, new: LiveGameState) -> None:
        with self._lock:
            self._state = new
        if self._on_update is not None:
            self._on_update(new)

    def handle_event(self, event: dict[str, Any]) -> LiveGameState:
        """Applica un evento e sincronizza la guardia fair play (§8.3).

        Esposto per i test: guida la sessione senza thread/rete.
        """
        new = reduce_event(self._state, event, me=self._state.me)
        if new.game_id and new.game_in_progress:
            self._gate.begin(new.game_id)              # partita in corso → analisi vietata
        elif self._state.game_in_progress and not new.game_in_progress:
            self._gate.end(self._state.game_id)        # partita finita → analisi sbloccata
        self._set(new)
        return new

    # ------------------------------------------------------------------ #
    # Azioni in partita (delegano alla Board API)
    # ------------------------------------------------------------------ #
    def seek(self, *, minutes: float, increment: int, rated: bool = False) -> LiveGameState:
        self._api.seek(minutes=minutes, increment=increment, rated=rated)
        self._set(self._state.model_copy(update={"status": "searching", "message": None}))
        return self.state

    def play_move(self, uci: str) -> LiveGameState:
        game_id = self._state.game_id
        if not game_id:
            raise ValueError("nessuna partita live in corso")
        self._api.play_move(game_id, uci)
        return self.state

    def resign(self) -> LiveGameState:
        if self._state.game_id:
            self._api.resign(self._state.game_id)
        return self.state

    def abort(self) -> LiveGameState:
        if self._state.game_id:
            self._api.abort(self._state.game_id)
        return self.state

    def offer_draw(self, *, accept: bool = True) -> LiveGameState:
        if self._state.game_id:
            self._api.offer_draw(self._state.game_id, accept=accept)
        return self.state

    # ------------------------------------------------------------------ #
    # Ciclo di streaming (thread)
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Avvia il thread che consuma lo stream eventi dell'account."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gmvibe-live", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for event in self._api.stream_events():
                if self._stop.is_set():
                    break
                if event.get("type") == "gameStart":
                    self.handle_event(event)
                    self._follow(event.get("game", {}).get("gameId"))
                else:
                    self.handle_event(event)
        except Exception as exc:  # noqa: BLE001 - lo stato va mostrato, non sollevato nel thread
            self._set(self._state.model_copy(update={"message": f"{type(exc).__name__}: {exc}"}))
        finally:
            # Rilascia la guardia SOLO se questa sessione ha davvero una partita attiva:
            # con game_id None, end(None) azzererebbe la guardia di un'ALTRA partita
            # in corso (bug fair-play: una sessione in sola ricerca sbloccava l'analisi).
            if self._state.game_id:
                self._gate.end(self._state.game_id)
            with self._lock:
                if self._state.game_in_progress:
                    self._state = self._state.model_copy(
                        update={"game_in_progress": False, "status": "finished"}
                    )
            self._set(self._state)

    def _follow(self, game_id: str | None) -> None:
        if not game_id:
            return
        for event in self._api.stream_game(game_id):
            if self._stop.is_set():
                break
            self.handle_event(event)
            if not self.state.game_in_progress:
                break

    def stop(self) -> None:
        """Ferma lo streaming e abbassa la guardia (chiamata esplicita/uscita).

        Rilascia la guardia solo se questa sessione possiede una partita attiva: fermare
        una ricerca (senza game_id) non deve sbloccare l'analisi di un'altra partita.
        """
        self._stop.set()
        if self._state.game_id:
            self._gate.end(self._state.game_id)
