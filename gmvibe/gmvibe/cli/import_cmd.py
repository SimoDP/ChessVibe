"""Comando ``import``: partite da Lichess/Chess.com/PGN → analisi in batch (§8.1, §10 FASE 7).

Scarica (o legge) un PGN multi-partita, lo divide in partite e — salvo ``--no-analyze`` —
le analizza tutte con la pipeline, salvando per ciascuna analisi, errori etichettati
(con ricorrenza §7) e puzzle (§7.1).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .. import config as cfg
from ..analysis.batch import analyze_many
from ..analysis.store import pgn_hash
from ..db import Database
from ..sources.pgn_loader import parse_pgn_text, split_pgn_games


def _load_combined_pgn(args: argparse.Namespace) -> tuple[str, str]:
    """Ritorna ``(pgn_multiplo, source)`` da Lichess, Chess.com o file locali."""
    sources = [s for s in ("lichess", "chesscom", "pgn") if getattr(args, s)]
    if len(sources) != 1:
        raise SystemExit("indicare esattamente una sorgente: --lichess/--chesscom/--pgn")

    if args.lichess:
        from ..sources.lichess_import import LichessImportError, fetch_games

        token = None
        try:
            secrets = cfg.load_secrets(args.profile) if args.profile else None
            if secrets is not None and secrets.lichess_token is not None:
                token = secrets.lichess_token.get_secret_value()
        except Exception:
            token = None
        try:
            return fetch_games(args.lichess, max_games=args.max, token=token), "lichess"
        except LichessImportError as exc:
            raise SystemExit(str(exc)) from exc

    if args.chesscom:
        from ..sources.chesscom_import import ChessComImportError, fetch_games

        try:
            return fetch_games(args.chesscom, max_games=args.max), "chesscom"
        except ChessComImportError as exc:
            raise SystemExit(str(exc)) from exc

    texts = []
    for path in args.pgn:
        texts.append(Path(path).read_text(encoding="utf-8"))
    return "\n\n".join(texts), "pgn"


def run(args: argparse.Namespace) -> int:
    # §8.3: nessuna analisi (nemmeno in batch) mentre una partita live e' in corso.
    from ..fairplay import FairPlayViolation, require_idle

    try:
        require_idle(action="import/analisi batch")
    except FairPlayViolation as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    if args.profile and args.profile not in cfg.list_profiles():
        print(
            f"Profilo '{args.profile}' non trovato. Crealo con "
            f"`python -m gmvibe init` (o usa --profile senza valore per il profilo di default).",
            file=sys.stderr,
        )
        return 1
    user_id = args.profile or "default"
    pgn_text, source = _load_combined_pgn(args)
    games = split_pgn_games(pgn_text)
    if not games:
        print("Nessuna partita trovata nella sorgente indicata.", file=sys.stderr)
        return 1
    print(f"Sorgente {source}: {len(games)} partite (profilo: {user_id})")

    db = Database()
    if args.no_analyze:
        saved = skipped = 0
        for text in games:
            if db.find_imported_by_hash(user_id, pgn_hash(text)) is not None:
                skipped += 1
                continue
            g = parse_pgn_text(text)
            db.save_imported_game(
                user_id, pgn_hash(text), source=source, white=g.white, black=g.black,
                result=g.result, cadenza=None, pgn_text=text,
            )
            saved += 1
        print(f"Salvate {saved} partite ({skipped} già presenti). Nessuna analisi (--no-analyze).")
        db.close()
        return 0

    levels = cfg.load_levels()
    if args.no_puzzles:
        levels = levels.model_copy(update={"puzzles": levels.puzzles.model_copy(update={"enabled": False})})

    t0 = time.perf_counter()
    items = analyze_many(
        games, db=db, user_id=user_id, profile_name=args.profile, nickname=args.profile,
        depth=args.depth, deep=not args.no_deep, source=source, levels=levels,
    )
    elapsed = time.perf_counter() - t0
    db.close()

    print(f"{'partita':<34} {'ris':<7} {'err':>4} {'puzzle':>6}  stato")
    print("-" * 66)
    for it in items:
        name = f"{it.white or '?'} - {it.black or '?'}"[:33]
        if it.skipped:
            state = "già analizzata"
        elif it.error:
            state = f"errore: {it.error[:40]}"
        else:
            state = "ok"
        print(f"{name:<34} {(it.result or '*'):<7} {it.error_count:>4} "
              f"{it.puzzle_count:>6}  {state}")

    ok = sum(1 for it in items if it.ok)
    errs = sum(1 for it in items if it.error)
    print(f"\n{ok} analizzate, {errs} errori, {sum(i.skipped for i in items)} saltate, "
          f"{sum(i.error_count for i in items)} errori etichettati, "
          f"{sum(i.puzzle_count for i in items)} puzzle | {elapsed:.2f}s")
    print("Ora: `python -m gmvibe profile` per il profilo di debolezze (§7).")
    return 0 if errs == 0 else 1
