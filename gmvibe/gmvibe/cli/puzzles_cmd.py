"""Comando ``puzzles``: elenca/genera gli esercizi dagli errori (documentazione.txt §7.1)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from .. import config as cfg
from ..db import Database
from ..models import GameAnalysis
from ..profile.puzzles import generate_puzzles


def _regenerate(db: Database, user_id: str, levels) -> int:
    """Rigenera i puzzle dalle analisi salvate, saltando quelli già presenti (fen+soluzione)."""
    existing = {(p["fen"], p["solution_uci"]) for p in db.list_puzzles(user_id)}
    created = 0
    for summary in db.list_analyses(user_id):
        raw = db.analysis_json(summary["id"])
        if raw is None:
            continue
        analysis = GameAnalysis.model_validate(json.loads(raw))
        for puzzle in generate_puzzles(
            analysis, user_id=user_id, game_id=summary["id"], config=levels.puzzles
        ):
            if (puzzle.fen, puzzle.solution_uci) in existing:
                continue
            db.save_puzzle(puzzle)
            existing.add((puzzle.fen, puzzle.solution_uci))
            created += 1
    return created


def run(args: argparse.Namespace) -> int:
    levels = cfg.load_levels()
    user_id = args.profile or "default"
    db = Database()
    try:
        if args.generate:
            n = _regenerate(db, user_id, levels)
            print(f"Generati {n} nuovi puzzle dai tuoi errori (§7.1).")

        due_before = datetime.now(timezone.utc).isoformat() if args.due else None
        puzzles = db.list_puzzles(user_id, due_before=due_before)
        if not puzzles:
            print("Nessun puzzle" + (" dovuto" if args.due else "") + ".")
            return 0

        print(f"{'id':>4} {'box':>3} {'tema':<20} {'fen':<42} soluzione")
        print("-" * 90)
        for p in puzzles:
            print(f"{p['id']:>4} {p.get('box', 1):>3} {(p.get('theme') or '-'):<20} "
                  f"{p['fen'][:40]:<42} {p.get('solution_san') or p['solution_uci']}")
        print(f"\n{len(puzzles)} puzzle. Ripasso: `python -m gmvibe review`.")
    finally:
        db.close()
    return 0
