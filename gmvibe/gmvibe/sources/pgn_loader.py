"""Parsing del PGN con estrazione dei tempi ``[%clk]`` (documentazione.txt §5.A).

I tempi servono per l'analisi del clock (§5.J). Lichess li include nell'export con
``clocks=true``. ``node.clock()`` di python-chess restituisce i secondi rimanenti dal
``[%clk H:MM:SS]``; se il clock manca (partite OTB) il valore e' ``None``.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import chess
import chess.pgn
from pydantic import BaseModel, Field

# Fallback: alcuni PGN hanno il clock solo nel commento, non parsato da python-chess.
_CLK_RE = re.compile(r"\[%clk\s+(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)\]")


class ParsedMove(BaseModel):
    ply: int                      # 1-based
    move_number: int
    color: str                    # "white" | "black"
    san: str
    uci: str
    fen_before: str
    fen_after: str
    clock_seconds: float | None = None
    clock_delta: float | None = None


class ParsedGame(BaseModel):
    headers: dict[str, str] = Field(default_factory=dict)
    result: str = "*"
    start_fen: str = chess.STARTING_FEN   # posizione iniziale (rispetta SetUp/FEN)
    moves: list[ParsedMove] = Field(default_factory=list)

    @property
    def white(self) -> str | None:
        return self.headers.get("White")

    @property
    def black(self) -> str | None:
        return self.headers.get("Black")


def _clock_from_comment(comment: str) -> float | None:
    m = _CLK_RE.search(comment or "")
    if not m:
        return None
    h, mm, ss = m.group(1), m.group(2), m.group(3)
    return int(h) * 3600 + int(mm) * 60 + float(ss)


def parse_pgn_text(text: str) -> ParsedGame:
    """Analizza una singola partita PGN e ne estrae mosse, header e tempi."""
    game = chess.pgn.read_game(io.StringIO(text))
    if game is None:
        raise ValueError("nessuna partita PGN trovata nel testo fornito")

    board = game.board()
    start_fen = board.fen()   # rispetta SetUp/FEN: la partita puo' iniziare da una posizione
    moves: list[ParsedMove] = []
    prev_clock: float | None = None

    for ply, node in enumerate(game.mainline(), start=1):
        move = node.move
        if move is None:  # pragma: no cover - mainline ha sempre mosse
            continue
        fen_before = board.fen()
        san = board.san(move)
        color = "white" if board.turn == chess.WHITE else "black"
        board.push(move)
        fen_after = board.fen()

        clock = node.clock()
        if clock is None:
            clock = _clock_from_comment(node.comment)
        delta = None
        if clock is not None and prev_clock is not None:
            delta = max(0.0, prev_clock - clock)  # tempo speso su questa mossa

        moves.append(
            ParsedMove(
                ply=ply,
                move_number=(ply + 1) // 2,
                color=color,
                san=san,
                uci=move.uci(),
                fen_before=fen_before,
                fen_after=fen_after,
                clock_seconds=clock,
                clock_delta=delta,
            )
        )
        if clock is not None:
            prev_clock = clock

    return ParsedGame(
        headers=dict(game.headers),
        result=game.headers.get("Result", "*"),
        start_fen=start_fen,
        moves=moves,
    )


def load_pgn(path: str | Path) -> ParsedGame:
    """Carica un file PGN (prima partita se ne contiene piu' di una)."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_pgn_text(text)


def iter_pgn_games(text: str):
    """Itera sui ``chess.pgn.Game`` contenuti in un testo PGN (multi-partita).

    Riusa lo stesso ``chess.pgn.read_game`` di :func:`parse_pgn_text`, cosi' il
    comportamento su PGN con ``SetUp``/``FEN`` resta identico.
    """
    stream = io.StringIO(text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            return
        yield game


def split_pgn_games(text: str) -> list[str]:
    """Divide un PGN multi-partita in una lista di testi PGN, uno per partita.

    Serve all'import in batch (§8.1): l'export di Lichess/Chess.com contiene molte
    partite concatenate e ognuna va analizzata separatamente dalla pipeline.
    Le partite non parsabili vengono saltate (testo vuoto o solo header spuri).
    """
    out: list[str] = []
    for game in iter_pgn_games(text):
        # ``str(game)`` riserializza una singola partita in PGN valido e autosufficiente.
        chunk = str(game).strip()
        if chunk:
            out.append(chunk)
    return out
