"""Comando ``review``: sessione di ripasso dilazionato (Leitner, documentazione.txt §7.1).

Mostra i puzzle dovuti uno alla volta sulla scacchiera testuale (la FEN), chiede la mossa
migliore in SAN o UCI, verifica con python-chess e aggiorna il box. La logica di
correttezza e di pianificazione sta in :mod:`gmvibe.profile.puzzles` (funzioni pure),
quindi è testabile senza input interattivo.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import chess

from .. import config as cfg
from ..db import Database
from ..profile.puzzles import box_due, next_box, solve


def _ask_move(fen: str) -> str | None:
    """Legge una mossa (SAN o UCI) e la normalizza in UCI; None per saltare."""
    raw = input("Mossa (SAN/UCI, invio per saltare): ").strip()
    if not raw:
        return None
    board = chess.Board(fen)
    try:  # prima UCI, poi SAN
        return chess.Move.from_uci(raw).uci()
    except ValueError:
        pass
    try:
        return board.parse_san(raw).uci()
    except ValueError:
        print("  Mossa non valida o illegale.")
        return None


def run(args: argparse.Namespace) -> int:
    levels = cfg.load_levels()
    intervals = levels.puzzles.leitner_intervals_days
    user_id = args.profile or "default"
    now = datetime.now(timezone.utc)

    db = Database()
    try:
        due = db.list_puzzles(user_id, due_before=now.isoformat())
        if args.limit:
            due = due[: args.limit]
        if not due:
            print("Nessun puzzle da ripassare. Ottimo!")
            return 0

        correct_total = 0
        answered = 0
        try:
            for i, p in enumerate(due, start=1):
                print(f"\n[{i}/{len(due)}] Puzzle #{p['id']} — tema: {p.get('theme') or '-'} "
                      f"(box {p.get('box', 1)})")
                print(f"  FEN: {p['fen']}")
                try:
                    print(chess.Board(p["fen"]).unicode(borders=True, empty_square="."))
                except ValueError:  # pragma: no cover - FEN sempre valida (dal motore)
                    pass
                if p.get("explanation"):
                    print(f"  {p['explanation']}")
                move = _ask_move(p["fen"])
                if move is None:
                    print("  saltato.")
                    continue
                ok = solve(move, p)
                answered += 1
                correct_total += int(ok)
                box = next_box(int(p.get("box") or 1), ok, intervals)
                due_at = box_due(box, intervals, now).isoformat()
                db.apply_review(p["id"], box=box, due_at=due_at, correct=ok)
                best = p.get("solution_san") or p["solution_uci"]
                print(f"  {'CORRETTO' if ok else 'SBAGLIATO'} — migliore: {best} "
                      f"→ prossimo box {box} il {due_at[:10]}")
        except (EOFError, KeyboardInterrupt):
            # Ctrl-D / Ctrl-C: chiude la sessione senza traceback, conservando i ripassi
            # gia' registrati.
            print("\nRipasso interrotto.")

        print(f"\nRipasso: {correct_total}/{answered} corretti su {answered} risposte.")
    finally:
        db.close()
    return 0
