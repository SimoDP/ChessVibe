"""Finali con tablebase (documentazione.txt §5.K).

Il verdetto esatto (vinto/patta/perso, DTZ) NON deve mai essere dedotto da Claude: viene
da qui. Fino a 5 pezzi si usano le Syzygy locali (``chess.syzygy``); fino a 7 pezzi si usa
l'API tablebase di Lichess.

Se non c'e' rete e non ci sono Syzygy locali, la funzione si disattiva e ritorna ``None``:
in nessun caso si inventa un verdetto.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import chess
import chess.syzygy

from ..models import TablebaseConfig, TablebaseVerdict

LICHESS_TABLEBASE_URL = "https://tablebase.lichess.ovh/standard?fen={fen}"

# Category dell'API Lichess -> categoria nostra.
_LICHESS_CATEGORY = {
    "win": "win", "draw": "draw", "loss": "loss",
    "cursed-win": "cursed_win", "blessed-loss": "blessed_loss",
    "unknown": "unknown", "maybe-win": "win", "maybe-loss": "loss",
}
# codici WDL di python-chess syzygy -> categoria (-2..2)
_SYZYGY_CATEGORY = {
    2: "win", 1: "cursed_win", 0: "draw", -1: "blessed_loss", -2: "loss",
}


def piece_count(board: chess.Board) -> int:
    return len(board.piece_map())


class TablebaseProbe:
    """Interroga le tablebase: Syzygy locali prima, poi l'API di Lichess."""

    def __init__(self, config: TablebaseConfig | None = None) -> None:
        self.config = config or TablebaseConfig()
        self._syzygy: chess.syzygy.Tablebase | None = None
        self._syzygy_loaded = False

    def _load_syzygy(self) -> chess.syzygy.Tablebase | None:
        if self._syzygy_loaded:
            return self._syzygy
        self._syzygy_loaded = True
        path = self.config.syzygy_path
        if path and Path(path).is_dir():
            try:
                self._syzygy = chess.syzygy.open_tablebase(path)
            except Exception:
                self._syzygy = None
        return self._syzygy

    def probe(self, board: chess.Board) -> TablebaseVerdict | None:
        """Verdetto esatto della posizione, o ``None`` se non disponibile."""
        if not self.config.enabled:
            return None
        pieces = piece_count(board)
        if pieces == 0:
            return None

        if pieces <= self.config.syzygy_max_pieces:
            verdict = self._probe_syzygy(board, pieces)
            if verdict is not None:
                return verdict

        if self.config.allow_network and pieces <= self.config.lichess_max_pieces:
            return self._probe_lichess(board, pieces)
        return None

    # ------------------------------------------------------------------ #
    def _probe_syzygy(self, board: chess.Board, pieces: int) -> TablebaseVerdict | None:
        tb = self._load_syzygy()
        if tb is None:
            return None
        try:
            wdl = tb.probe_wdl(board)
            dtz = tb.probe_dtz(board)
        except (KeyError, chess.syzygy.MissingTableError):
            return None
        # La mossa migliore: quella che massimizza il WDL per chi muove.
        best = _best_move_by_wdl(board, tb)
        # python-chess restituisce WDL e DTZ dal punto di vista di chi muove.
        return TablebaseVerdict(
            fen=board.fen(), pieces=pieces,
            category=_SYZYGY_CATEGORY.get(wdl, "unknown"),
            dtz=dtz,
            best_move_uci=best.uci() if best else None,
            source="syzygy",
        )

    def _probe_lichess(self, board: chess.Board, pieces: int) -> TablebaseVerdict | None:
        fen = board.fen().replace(" ", "_")
        try:
            with urllib.request.urlopen(LICHESS_TABLEBASE_URL.format(fen=fen), timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        # L'API Lichess restituisce la categoria dal punto di vista di CHI MUOVE (verificato:
        # con il Nero al tratto in posizione persa ritorna "loss"), che e' la convenzione
        # che usiamo. Il campo `checkmate` significa che chi muove e' matto.
        if data.get("checkmate"):
            category = "loss"
        elif data.get("stalemate") or data.get("insufficient_material"):
            category = "draw"
        else:
            category = _LICHESS_CATEGORY.get(data.get("category", "unknown"), "unknown")

        dtz = data.get("dtz")
        dtm = data.get("dtm")
        best = None
        moves = data.get("moves") or []
        if moves:
            # la prima mossa e' la migliore; l'API la ordina per qualita'
            best = moves[0].get("uci")

        return TablebaseVerdict(
            fen=board.fen(), pieces=pieces, category=category,
            dtz=dtz, dtm=dtm, best_move_uci=best, source="lichess",
        )


def _best_move_by_wdl(board: chess.Board, tb: chess.syzygy.Tablebase) -> chess.Move | None:
    """Mossa che massimizza il WDL per chi muove (dalle Syzygy locali)."""
    best_move = None
    best_wdl = -3
    for move in board.legal_moves:
        b = board.copy(stack=False)
        b.push(move)
        try:
            # WDL dopo la mossa, dal punto di vista di chi ha mosso
            child_wdl = -tb.probe_wdl(b)
        except (KeyError, chess.syzygy.MissingTableError):
            continue
        if child_wdl > best_wdl:
            best_wdl = child_wdl
            best_move = move
    return best_move


_INVERSE = {
    "win": "loss", "loss": "win",
    "cursed_win": "blessed_loss", "blessed_loss": "cursed_win",
    "draw": "draw", "unknown": "unknown",
}


def _invert(category: str) -> str:
    return _INVERSE.get(category, "unknown")


def white_pov_category(v: TablebaseVerdict | None, board: chess.Board) -> str | None:
    """Categoria dal punto di vista del Bianco (per confrontare prima/dopo)."""
    if v is None:
        return None
    return v.category if board.turn == chess.WHITE else _invert(v.category)
