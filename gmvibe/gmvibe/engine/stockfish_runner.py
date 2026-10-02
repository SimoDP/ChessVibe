"""Wrapper Stockfish su python-chess (documentazione.txt §3, §5.B, §5.D).

Fatti API verificati sulla versione installata (python-chess 1.11.2, Stockfish 15.1):

- ``engine.analyse(board, Limit(...), multipv=N, root_moves=[...])`` restituisce SEMPRE
  una ``list[InfoDict]`` (anche con multipv=1) se ``multipv`` e' passato.
- ``MultiPV`` NON va impostato via ``configure``: e' "automatically managed" e solleva
  ``EngineError``. Si passa come argomento a ``analyse``.
- ``info["score"]`` e' un ``PovScore`` con ``.white()`` / ``.turn`` / ``.is_mate()`` /
  ``.mate()`` / ``.score()``.
- Con ``UCI_ShowWDL=true``, ``info["wdl"]`` e' un ``PovWdl`` con ``.white()`` / ``.turn``
  che restituiscono ``Wdl(wins, draws, losses)`` per mille (somma 1000).
- ``node.clock()`` (PGN) restituisce i secondi rimanenti dal ``[%clk]``.
- ``engine.id`` e' un dict ``{"name": ..., "author": ...}``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import chess
import chess.engine
from chess.engine import Limit, SimpleEngine

from .. import paths
from ..models import EngineEval, PVLine, WDL

# Percorsi comuni in cui finisce il pacchetto Stockfish (Debian/Ubuntu: /usr/games).
_COMMON_PATHS = ("/usr/games/stockfish", "/usr/local/bin/stockfish", "/usr/bin/stockfish")


def find_stockfish() -> str | None:
    """Trova il binario Stockfish: env ``GMVIBE_STOCKFISH``, PATH, poi percorsi comuni."""
    env = os.environ.get(paths.ENV_STOCKFISH)
    if env and Path(env).exists():
        return env
    found = shutil.which("stockfish")
    if found:
        return found
    for candidate in _COMMON_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def resolve_threads(threads: int | None) -> int | None:
    """Traduce ``threads`` di config in un valore per Stockfish.

    ``None`` -> lascia il default del motore (una thread); ``0`` -> tutti i core
    disponibili; un intero positivo -> quel numero di thread.
    """
    if threads is None:
        return None
    if threads <= 0:
        return os.cpu_count() or 1
    return threads


def build_runner(engine_config: object | None = None) -> "StockfishRunner":
    """Costruisce un :class:`StockfishRunner` dalle risorse in config (§3, CLAUDE.md).

    Accetta un :class:`~gmvibe.models.EngineConfig` (o ``None`` per i default del
    motore). Unica factory usata da pipeline e batch: nessun valore hardcoded.
    """
    threads = getattr(engine_config, "threads", None)
    hash_mb = getattr(engine_config, "hash_mb", None)
    return StockfishRunner(
        threads=resolve_threads(threads),
        hash_mb=hash_mb if hash_mb else None,
    )


class StockfishRunner:
    """Esegue analisi UCI su una board. Usare come context manager.

    Esempio::

        with StockfishRunner(threads=2, hash_mb=128) as runner:
            ev = runner.analyse_position(board, depth=14, multipv=1)
    """

    def __init__(
        self,
        path: str | None = None,
        *,
        threads: int | None = None,
        hash_mb: int | None = None,
        show_wdl: bool = True,
    ) -> None:
        self.path = path or find_stockfish()
        if not self.path:
            raise FileNotFoundError(
                "Stockfish non trovato. Imposta GMVIBE_STOCKFISH o installalo "
                "(apt install stockfish). Vedi README."
            )
        self._threads = threads
        self._hash_mb = hash_mb
        self._show_wdl = show_wdl
        self._engine: SimpleEngine | None = None

    # ------------------------------------------------------------------ #
    # Ciclo di vita
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "StockfishRunner":
        self._engine = chess.engine.SimpleEngine.popen_uci(self.path)
        options: dict[str, object] = {}
        if self._show_wdl and "UCI_ShowWDL" in self._engine.options:
            options["UCI_ShowWDL"] = True
        if self._threads is not None and "Threads" in self._engine.options:
            options["Threads"] = self._threads
        if self._hash_mb is not None and "Hash" in self._engine.options:
            options["Hash"] = self._hash_mb
        if options:
            self._engine.configure(options)
        return self

    def __exit__(self, *exc) -> None:
        self.quit()

    def quit(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            finally:
                self._engine = None

    @property
    def engine(self) -> SimpleEngine:
        if self._engine is None:
            raise RuntimeError("StockfishRunner non avviato: usalo come context manager")
        return self._engine

    @property
    def engine_id(self) -> dict[str, str]:
        return dict(self.engine.id)

    def engine_version_key(self) -> str:
        """Stringa usata come componente della chiave di cache (§5.D).

        Include nome e versione: aggiornare Stockfish invalida automaticamente la cache.
        """
        ident = self.engine.id
        name = ident.get("name", "stockfish")
        author = ident.get("author", "")
        return f"{name} ({author})" if author else name

    # ------------------------------------------------------------------ #
    # Analisi
    # ------------------------------------------------------------------ #
    def analyse_position(
        self,
        board: chess.Board,
        *,
        depth: int | None = None,
        time: float | None = None,
        multipv: int = 1,
        root_moves: list[chess.Move] | None = None,
    ) -> EngineEval:
        """Analizza una posizione e restituisce un :class:`EngineEval`.

        ``root_moves`` limita l'analisi alle mosse indicate: serve a VALUTARE SEMPRE la
        mossa effettivamente giocata anche se non e' tra le prime MultiPV (§5.D).
        """
        if depth is None and time is None:
            time = 0.3
        limit = Limit(depth=depth, time=time)
        kwargs: dict[str, object] = {"multipv": multipv}
        if root_moves:
            kwargs["root_moves"] = root_moves
        infos = self.engine.analyse(board, limit, **kwargs)
        return self._build_eval(board, infos, multipv, root_moves)

    def fast_pass(self, board: chess.Board, *, depth: int = 14, time: float = 0.3,
                  use_wdl: bool = True) -> EngineEval:
        """Pass veloce (§5.B): MultiPV=1, WDL attivo, profondita' ~14 o ~0.3s."""
        return self.analyse_position(
            board, depth=depth if depth else None, time=None if depth else time, multipv=1
        )

    def analysed_move(self, board: chess.Board, move: chess.Move,
                      *, depth: int | None = None, time: float | None = None) -> EngineEval:
        """Valuta la mossa giocata via ``root_moves`` (§5.D). Restituisce 1 linea."""
        return self.analyse_position(
            board, depth=depth, time=time, multipv=1, root_moves=[move]
        )

    # ------------------------------------------------------------------ #
    # Costruzione dei modelli
    # ------------------------------------------------------------------ #
    def _build_eval(
        self,
        board: chess.Board,
        infos,
        multipv: int,
        root_moves: list[chess.Move] | None,
    ) -> EngineEval:
        info_list = self._as_info_list(infos)
        turn = "white" if board.turn == chess.WHITE else "black"
        lines: list[PVLine] = []
        for info in info_list:
            lines.append(self._build_line(board, info))
        ident = self.engine.id
        # profondita' effettiva raggiunta (la prima linea la rappresenta)
        reached_depth = int(info_list[0].get("depth", 0)) if info_list else 0
        return EngineEval(
            fen=board.fen(),
            depth=reached_depth,
            multipv=multipv,
            engine_name=ident.get("name", "stockfish"),
            engine_version=self.engine_version_key(),
            turn=turn,
            lines=lines,
        )

    @staticmethod
    def _as_info_list(infos) -> list:
        """Normalizza l'output di ``analyse``: sempre una lista di InfoDict."""
        if isinstance(infos, list):
            return infos
        return [infos]

    @staticmethod
    def _build_line(board: chess.Board, info) -> PVLine:
        pov = info["score"]
        white_score = pov.white()
        mate = white_score.mate()
        score_cp = white_score.score()
        pv = list(info.get("pv", []))
        move_uci = pv[0].uci() if pv else ""
        move_san = None
        if pv:
            try:
                move_san = board.san(pv[0])
            except (ValueError, AssertionError):
                move_san = None
        return PVLine(
            rank=int(info.get("multipv", 1)),
            move_uci=move_uci,
            move_san=move_san,
            score_cp=score_cp,
            mate=mate,
            wdl=_extract_wdl(info),
            pv_uci=[m.uci() for m in pv],
        )


def _extract_wdl(info) -> WDL | None:
    """Estrae i conteggi WDL dal POV del Bianco.

    Preferisce ``info["wdl"]`` (PovWdl, presente con ``UCI_ShowWDL=true``); se assente
    restituisce ``None``. Isolato per poter testare quale percorso e' scattato.
    """
    pov_wdl = info.get("wdl")
    if pov_wdl is None:
        return None
    white = pov_wdl.white()
    return WDL(wins=white.wins, draws=white.draws, losses=white.losses)
