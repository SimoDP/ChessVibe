"""Entry point CLI: ``python -m gmvibe <init|analyze>`` (documentazione.txt §10 FASE 1)."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmvibe",
        description="GM-Vibe — analisi scacchistica di livello magistrale.",
    )
    parser.add_argument("--version", action="version", version=f"gmvibe {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="wizard di configurazione iniziale (§4.5)")
    p_init.set_defaults(func=_run_init)

    p_an = sub.add_parser("analyze", help="analizza una partita PGN (pass veloce)")
    p_an.add_argument("pgn", help="file PGN da analizzare")
    p_an.add_argument("--profile", default=None, help="nickname del profilo per la calibrazione")
    p_an.add_argument("--depth", type=int, default=None, help="profondita' del pass veloce")
    p_an.add_argument("--time", type=float, default=None, help="secondi per posizione (se --depth assente)")
    p_an.add_argument("--deep-depth", type=int, default=None, help="profondita' del pass profondo (default: levels.yaml)")
    p_an.add_argument("--no-deep", action="store_true", help="salta il pass profondo sui candidati")
    p_an.add_argument("--refresh", action="store_true", help="ignora la cache e ricalcola")
    p_an.add_argument(
        "--save", action="store_true",
        help="salva analisi, errori etichettati e puzzle nel DB (§7)",
    )
    p_an.add_argument(
        "--coach", action="store_true",
        help="esegue il layer LLM (Maestro virtuale, §6) sul risultato dell'analisi",
    )
    p_an.add_argument(
        "--maia", action="store_true",
        help="usa Maia via Lc0 per il fattore 5 del Complexity Score (§5.H, FASE 8)",
    )
    p_an.set_defaults(func=_run_analyze)

    p_serve = sub.add_parser("serve", help="avvia la UI di review nel browser (§9)")
    p_serve.add_argument("--host", default="127.0.0.1", help="indirizzo di ascolto")
    p_serve.add_argument("--port", type=int, default=8000, help="porta (default 8000)")
    p_serve.add_argument("--reload", action="store_true", help="ricarica a caldo (sviluppo)")
    p_serve.set_defaults(func=_run_serve)

    # --- FASE 7: import + cross-partita (§7, §8.1) ---
    p_imp = sub.add_parser("import", help="importa e analizza partite (Lichess/Chess.com/PGN)")
    src = p_imp.add_mutually_exclusive_group(required=True)
    src.add_argument("--lichess", metavar="USER", help="nickname Lichess (partite pubbliche)")
    src.add_argument("--chesscom", metavar="USER", help="username Chess.com")
    src.add_argument("--pgn", metavar="FILE", nargs="+", help="uno o più file PGN (anche multi-partita)")
    p_imp.add_argument("--max", type=int, default=20, help="numero massimo di partite (default 20)")
    p_imp.add_argument("--profile", default=None, help="nickname del profilo (user_id e calibrazione)")
    p_imp.add_argument("--depth", type=int, default=None, help="profondita' del pass veloce")
    p_imp.add_argument("--no-deep", action="store_true", help="salta il pass profondo")
    p_imp.add_argument("--no-analyze", action="store_true", help="salva solo i PGN, senza analizzarli")
    p_imp.add_argument("--no-puzzles", action="store_true", help="non generare i puzzle (§7.1)")
    p_imp.set_defaults(func=_run_import)

    p_prof = sub.add_parser("profile", help="profilo di debolezze cross-partita (§7)")
    p_prof.add_argument("--profile", default=None, help="nickname del profilo")
    p_prof.add_argument("--last", type=int, default=None, help="finestra: ultime N partite")
    p_prof.set_defaults(func=_run_profile)

    p_puz = sub.add_parser("puzzles", help="puzzle generati dagli errori (§7.1)")
    p_puz.add_argument("--profile", default=None, help="nickname del profilo")
    p_puz.add_argument("--generate", action="store_true", help="rigenera i puzzle dalle analisi")
    p_puz.add_argument("--due", action="store_true", help="elenca solo i puzzle da ripassare")
    p_puz.set_defaults(func=_run_puzzles)

    p_rev = sub.add_parser("review", help="sessione di ripasso dilazionato (Leitner, §7.1)")
    p_rev.add_argument("--profile", default=None, help="nickname del profilo")
    p_rev.add_argument("--limit", type=int, default=None, help="numero massimo di puzzle")
    p_rev.set_defaults(func=_run_review)

    # --- FASE 8: gioco live su Lichess (§8.2-8.3) ---
    p_live = sub.add_parser("live", help="gioca una partita live su Lichess (Board API, §8.2)")
    p_live.add_argument("--profile", default=None, help="profilo con il token Lichess (board:play)")
    p_live.add_argument("--minutes", type=float, default=None, help="minuti (default da levels.yaml)")
    p_live.add_argument("--increment", type=int, default=None, help="incremento in secondi")
    p_live.add_argument("--rated", action="store_true", help="partita rated (richiede scope board:play)")
    p_live.set_defaults(func=_run_live)

    return parser


def _run_init(args: argparse.Namespace) -> int:
    from .cli import init_wizard

    return init_wizard.run(args)


def _run_analyze(args: argparse.Namespace) -> int:
    from .cli import analyze

    return analyze.run(args)


def _run_serve(args: argparse.Namespace) -> int:
    from .cli import serve

    return serve.run(args)


def _run_import(args: argparse.Namespace) -> int:
    from .cli import import_cmd

    return import_cmd.run(args)


def _run_profile(args: argparse.Namespace) -> int:
    from .cli import profile_cmd

    return profile_cmd.run(args)


def _run_puzzles(args: argparse.Namespace) -> int:
    from .cli import puzzles_cmd

    return puzzles_cmd.run(args)


def _run_review(args: argparse.Namespace) -> int:
    from .cli import review_cmd

    return review_cmd.run(args)


def _run_live(args: argparse.Namespace) -> int:
    from .cli import live_cmd

    return live_cmd.run(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
