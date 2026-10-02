"""Analisi in batch di più partite (documentazione.txt §7, §8.1, §10 FASE 7).

Prende una lista di PGN (una partita ciascuno, già divisi da
``sources.pgn_loader.split_pgn_games``), li analizza uno per uno con la pipeline
esistente e persiste ognuno con :func:`gmvibe.analysis.store.persist_analysis`.

Un solo motore condiviso per tutto il batch; le partite già importate (stesso hash) si
saltano, così rieseguire l'import è economico. Accettazione §10: "analisi in batch delle
ultime 20 partite".
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config as cfg
from ..db import Database
from ..engine.stockfish_runner import StockfishRunner, build_runner
from ..sources.pgn_loader import parse_pgn_text
from .pipeline import analyze_game
from .store import persist_analysis, pgn_hash

__all__ = ["BatchItem", "analyze_many"]


@dataclass
class BatchItem:
    """Esito dell'analisi di una singola partita del batch."""

    pgn_hash: str
    white: str | None = None
    black: str | None = None
    result: str | None = None
    analysis_id: int | None = None
    error_count: int = 0
    puzzle_count: int = 0
    skipped: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.skipped


def analyze_many(
    pgns: list[str],
    *,
    db: Database,
    user_id: str,
    profile_name: str | None = None,
    nickname: str | None = None,
    depth: int | None = None,
    deep: bool = True,
    source: str = "pgn",
    skip_existing: bool = True,
    runner: StockfishRunner | None = None,
    levels=None,
    maia_client: object | None = None,
) -> list[BatchItem]:
    """Analizza e persiste una lista di PGN. Ritorna un :class:`BatchItem` per partita."""
    levels = levels if levels is not None else cfg.load_levels()
    own_runner = runner is None
    engine = runner or build_runner(levels.engine)
    if own_runner:
        engine.__enter__()

    items: list[BatchItem] = []
    try:
        for text in pgns:
            text = (text or "").strip()
            if not text:
                continue
            item = BatchItem(pgn_hash=pgn_hash(text))
            try:
                parsed = parse_pgn_text(text)  # header a costo zero, per la tabella
                item.white, item.black, item.result = parsed.white, parsed.black, parsed.result
            except Exception:  # pragma: no cover - header opzionali
                pass

            if skip_existing and db.find_imported_by_hash(user_id, item.pgn_hash) is not None:
                item.skipped = True
                items.append(item)
                continue

            try:
                analysis, _stats = analyze_game(
                    text, profile_name=profile_name, depth=depth, deep=deep,
                    db=db, runner=engine, maia_client=maia_client,
                )
                if not analysis.rows:
                    raise ValueError("il PGN non contiene mosse valide")
                item.white, item.black, item.result = (
                    analysis.white or item.white, analysis.black or item.black,
                    analysis.result or item.result,
                )
                result = persist_analysis(
                    db, user_id, pgn_text=text, analysis=analysis, levels=levels,
                    source=source, nickname=nickname or profile_name,
                )
                item.analysis_id = result.analysis_id
                item.error_count = result.error_count
                item.puzzle_count = result.puzzle_count
            except Exception as exc:
                item.error = str(exc)
            items.append(item)
    finally:
        if own_runner:
            engine.__exit__(None, None, None)
    return items
