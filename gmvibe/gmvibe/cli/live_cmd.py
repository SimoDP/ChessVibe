"""Comando ``live``: partita live su Lichess da terminale (§8.2-8.3).

Cerca una partita con la Board API, mostra la scacchiera e legge le mosse da stdin
(SAN o UCI), oltre ai comandi ``resign``/``draw``/``quit``. **Mentre la partita e' in
corso ogni analisi e' vietata dal codice** (§8.3): lo segnala il banner in alto e, se in
un altro terminale si tentasse ``gmvibe analyze`` o ``--coach``, fallirebbe.

Serve un profilo con token Lichess (scope ``board:play``) in ``secrets.env``. Mai
password, mai il token in chiaro (§4.4).
"""

from __future__ import annotations

import sys

import chess

from .. import config as cfg
from ..fairplay import gate
from ..models import LiveGameState

__all__ = ["run"]


def _render(state: LiveGameState) -> str:
    board = chess.Board(state.fen)
    turn = "Bianco" if board.turn else "Nero"
    mine = " (tocca a te)" if state.my_color == ("white" if board.turn else "black") else ""
    cw = f"{state.clock_white:.0f}s" if state.clock_white is not None else "-"
    cb = f"{state.clock_black:.0f}s" if state.clock_black is not None else "-"
    return (
        f"\n{board}\n"
        f"{turn}{mine} | tu: {state.my_color or '?'} vs {state.opponent or '?'} | "
        f"clock B {cw} / N {cb} | mosse: {' '.join(state.moves) or '—'}"
    )


def run(args) -> int:
    from ..lichess_live import BoardAPI, LichessLiveError, LiveGameSession

    levels = cfg.load_levels()
    if not levels.live.enabled:
        print("Gioco live disattivato (levels.yaml: live.enabled=false).", file=sys.stderr)
        return 1

    token = None
    if args.profile:
        try:
            secrets = cfg.load_secrets(args.profile)
            if secrets.lichess_token is not None:
                token = secrets.lichess_token.get_secret_value()
        except Exception:
            token = None
    try:
        api = BoardAPI(token)
    except LichessLiveError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2

    me = None
    try:
        me = api.account().get("username")
    except Exception:  # noqa: BLE001 - l'account e' solo per dedurre il colore
        pass

    session = LiveGameSession(api, me=me, levels=levels.live, on_update=lambda s: None)
    minutes = args.minutes if args.minutes is not None else levels.live.default_minutes
    increment = args.increment if args.increment is not None else levels.live.default_increment
    rated = args.rated or levels.live.default_rated
    try:
        session.seek(minutes=minutes, increment=increment, rated=rated)
    except LichessLiveError as exc:
        print(f"Ricerca fallita: {exc}", file=sys.stderr)
        return 2

    print(f"Ricerca: {minutes:g}+{increment} {'rated' if rated else 'casual'} … (Ctrl-C per uscire)")
    session.start()

    try:
        _loop(session, me)
    except (KeyboardInterrupt, EOFError):
        print("\nInterrotto.")
    finally:
        session.stop()
        print(f"Guardia fair play: {gate().as_dict()}")
    return 0


def _loop(session: LiveGameSession, me: str | None) -> None:
    """Ciclo di gioco: stampa lo stato e legge mosse/comandi da stdin."""
    last_shown: LiveGameState | None = None
    while True:
        state = session.state
        if state is not last_shown and state.status != "idle":
            print(_render(state))
            if state.game_in_progress:
                print("⛔ fair play: nessuna analisi mentre giochi (§8.3)")
            last_shown = state
        if state.status == "finished":
            print(f"Partita finita ({state.result or state.winner or 'fine'}).")
            return
        try:
            line = input("mossa (SAN/UCI) | resign | draw | quit > ").strip()
        except EOFError:
            return
        if not line:
            continue
        low = line.lower()
        if low in ("quit", "exit", "q"):
            return
        if low in ("resign", "resa"):
            session.resign()
            continue
        if low in ("draw", "patta", "patto"):
            session.offer_draw(accept=True)
            continue
        if not state.game_in_progress:
            print("Nessuna partita in corso: in attesa che ne inizi una…")
            continue
        uci = _to_uci(line, state)
        if uci is None:
            print(f"Mossa non valida: {line}")
            continue
        session.play_move(uci)


def _to_uci(text: str, state: LiveGameState) -> str | None:
    """Converte SAN o UCI in UCI legale sulla posizione corrente."""
    board = chess.Board(state.fen)
    try:
        move = chess.Move.from_uci(text)
    except ValueError:
        try:
            move = board.parse_san(text)
        except (ValueError, chess.IllegalMoveError):
            return None
    return move.uci() if move in board.legal_moves else None
