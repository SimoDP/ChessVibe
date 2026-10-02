"""Orchestrazione della persistenza di un'analisi (documentazione.txt §7, §10 FASE 7).

Un **unico punto** (usato da CLI e web) che, data una ``GameAnalysis`` già calcolata:

1. seleziona i momenti critici (§5.I) e li assegna all'analisi;
2. salva l'analisi completa (tabella ``analyses``, FASE 6);
3. conserva il PGN grezzo importato (``imported_games``, §8.1);
4. per ogni errore etichettato conta le **partite precedenti** col medesimo pattern (§7),
   scrive ``recurrence_games`` su righe e momenti e salva gli errori (``errors``);
5. genera i puzzle dagli errori significativi (§7.1) e li salva con il ripasso (box 1).

Il motore ha già calcolato tutto: qui si **misura e si conserva**, non si ricalcola.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from ..ai_coach.moments import select_critical_moments
from ..db import Database
from ..models import GameAnalysis, LevelsConfig
from ..profile.puzzles import generate_puzzles
from ..profile.weaknesses import recurrence_by_label

__all__ = ["PersistResult", "persist_analysis", "pgn_hash"]


def pgn_hash(text: str) -> str:
    """Hash stabile del testo PGN (per dedup e per la colonna ``pgn_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class PersistResult:
    """Esito della persistenza di una partita."""

    analysis_id: int
    error_count: int = 0
    puzzle_count: int = 0
    recurrence: dict[str, int] = field(default_factory=dict)
    reused: bool = False  # True = ri-analisi: riga esistente aggiornata, non duplicata

    def as_dict(self) -> dict:
        return {
            "analysis_id": self.analysis_id,
            "error_count": self.error_count,
            "puzzle_count": self.puzzle_count,
            "recurrence": self.recurrence,
            "reused": self.reused,
        }


def _player_color(analysis: GameAnalysis, nickname: str | None) -> str | None:
    if not nickname:
        return None
    nick = nickname.strip().lower()
    if (analysis.white or "").strip().lower() == nick:
        return "white"
    if (analysis.black or "").strip().lower() == nick:
        return "black"
    return None


def _cadenza(analysis: GameAnalysis) -> str:
    if analysis.time is not None and analysis.time.cadenza:
        return analysis.time.cadenza
    return "blitz"


def persist_analysis(
    db: Database,
    user_id: str,
    *,
    pgn_text: str,
    analysis: GameAnalysis,
    levels: LevelsConfig,
    source: str = "pgn",
    nickname: str | None = None,
) -> PersistResult:
    """Salva analisi, PGN grezzo, errori etichettati (con ricorrenza) e puzzle (§7).

    La persistenza è **idempotente**: se (``user_id``, ``pgn_hash``) è già presente, la
    riga esistente viene aggiornata (analisi, errori e puzzle ricreati) invece di
    generarne una nuova — ri-analizzare la stessa partita non produce duplicati. Lo stato
    di ripasso (Leitner) dei puzzle identici viene conservato.

    Va chiamata **dopo** ``analyze_game``. ``nickname`` serve a scegliere il lato del
    giocatore per i momenti critici; ``source`` è ``pgn`` | ``lichess`` | ``chesscom``.
    """
    # 1. Momenti critici (§5.I) — servono alla UI e all'LLM.
    analysis.moments = select_critical_moments(
        analysis,
        max_moments=levels.coach.max_moments,
        player_color=_player_color(analysis, nickname),
    )

    # 2. Riga ``analyses``: nuova, oppure riusata se la partita è già stata analizzata.
    h = pgn_hash(pgn_text)
    existing = db.find_analysis_by_hash(user_id, h)
    provisional = json.dumps(analysis.model_dump(), ensure_ascii=False)
    # (fen, soluzione) -> stato Leitner, per non perdere il ripasso sui puzzle identici.
    restored_reviews: dict[tuple[str | None, str | None], dict] = {}
    if existing is not None:
        analysis_id = int(existing["id"])
        for row in db.delete_puzzles_for_game(user_id, analysis_id):
            restored_reviews[(row["fen"], row["solution_uci"])] = row
        db.delete_errors_for_game(user_id, analysis_id)
        db.update_analysis(
            analysis_id, white=analysis.white, black=analysis.black,
            result=analysis.result, analysis_json=provisional,
        )
    else:
        analysis_id = db.save_analysis(
            user_id, h, white=analysis.white, black=analysis.black,
            result=analysis.result, analysis_json=provisional,
        )

    # 3. PGN grezzo: ri-analisi/export/dedup senza tornare in rete (una sola riga).
    if db.find_imported_by_hash(user_id, h) is None:
        db.save_imported_game(
            user_id, h, source=source, white=analysis.white,
            black=analysis.black, result=analysis.result, cadenza=_cadenza(analysis),
            pgn_text=pgn_text,
        )

    # 4. Ricorrenza (§7): quante partite PRECEDENTI hanno lo stesso error_label.
    cadenza = _cadenza(analysis)
    error_rows = [r for r in analysis.rows if r.label != "ok" and r.error_label]
    labels = [r.error_label for r in error_rows if r.error_label]
    recurrence = recurrence_by_label(db, user_id, analysis_id, labels)
    for row in error_rows:
        row.recurrence_games = recurrence.get(row.error_label or "", 0)
    for moment in analysis.moments:
        moment.recurrence_games = recurrence.get(moment.error_label or "", 0)

    # Il JSON salvato va aggiornato coi valori di ricorrenza ora noti.
    db.update_analysis_json(
        analysis_id, json.dumps(analysis.model_dump(), ensure_ascii=False)
    )

    errors = [
        {
            "ply": r.ply, "move_number": r.move_number, "color": r.color, "phase": r.phase,
            "label": r.label, "error_label": r.error_label, "san": r.san,
            "fen_before": r.fen_before, "opening_name": r.opening_name,
            "opening_eco": r.opening_eco, "cadenza": cadenza,
            "clock_seconds": r.clock_seconds, "winpct_loss": r.winpct_loss,
            "recurrence_prior": recurrence.get(r.error_label or "", 0),
        }
        for r in error_rows
    ]
    db.save_errors(user_id, analysis_id, errors)

    # 5. Puzzle (§7.1) dagli errori significativi. Un puzzle identico a uno precedente
    #    (stessa posizione e stessa soluzione) riprende il suo stato di ripasso.
    puzzles = generate_puzzles(
        analysis, user_id=user_id, game_id=analysis_id, config=levels.puzzles
    )
    for puzzle in puzzles:
        db.save_puzzle(puzzle, restored_reviews.get((puzzle.fen, puzzle.solution_uci)))

    return PersistResult(
        analysis_id=analysis_id, error_count=len(errors),
        puzzle_count=len(puzzles), recurrence=recurrence,
        reused=existing is not None,
    )
