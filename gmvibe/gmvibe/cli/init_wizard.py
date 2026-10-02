"""Wizard di primo avvio: ``python -m gmvibe init`` (documentazione.txt §4.5).

Guida l'utente passo per passo e scrive profilo e segreti. NON chiede mai la password di
Lichess/Chess.com: solo token (§4.4). L'utente inserisce SOLO il rating: ogni parametro
dipendente dal livello passa da ``resolve_level``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import config as cfg
from .. import paths
from ..level import resolve_level
from ..models import Account, PlayerProfile, Repertorio, Secrets

_YES = {"y", "yes", "s", "si", "sì"}


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def _ask_int(prompt: str, default: int | None = None) -> int:
    while True:
        raw = _ask(prompt, str(default) if default is not None else None)
        try:
            return int(raw)
        except ValueError:
            print("  Valore non valido: inserisci un numero intero.")


def _ask_choice(prompt: str, choices: list[str], default: str) -> str:
    shown = "/".join(choices)
    while True:
        raw = _ask(f"{prompt} ({shown})", default).lower()
        if raw in choices:
            return raw
        print(f"  Scelta non valida. Opzioni: {shown}.")


def run(args: argparse.Namespace) -> int:
    from ..engine.stockfish_runner import find_stockfish

    profiles_dir = paths.profiles_dir()
    profiles_dir.mkdir(parents=True, exist_ok=True)

    print("== GM-Vibe — configurazione iniziale ==\n")

    # 1. Nickname e lingua del report
    nickname = _ask("Nickname")
    if not nickname:
        print("Il nickname e' obbligatorio.")
        return 2
    lingua = _ask("Lingua del report", "it")

    # 2. Rating, tipo, cadenza -> resolve_level calcola tutto il resto
    rating = _ask_int("Rating")
    rating_tipo = _ask_choice("Tipo di rating", ["fide", "lichess", "chesscom", "altro"], "fide")
    cadenza = _ask_choice("Cadenza preferita", ["blitz", "rapid", "classica"], "blitz")

    profile = PlayerProfile(
        nickname=nickname,
        lingua_report=lingua,
        rating=rating,
        rating_tipo=rating_tipo,
        cadenza_preferita=cadenza,
    )

    levels = cfg.load_levels()
    conversion = cfg.load_rating_conversion()
    level_config = resolve_level(profile, levels, conversion)

    print("\n-- Calibrazione calcolata --")
    print(f"  {level_config.calibration_line}")
    print(f"  soglie (win%): imprecisione {level_config.error_thresholds.imprecisione}, "
          f"errore {level_config.error_thresholds.errore}, "
          f"svarione {level_config.error_thresholds.svarione}")
    print(f"  momenti critici: {level_config.critical_moments}")
    if not _confirm("Confermi questi parametri?", default=True):
        print("Configurazione annullata.")
        return 1

    # 3. Username Lichess / Chess.com (facoltativi)
    lichess_user = _ask("Username Lichess (facoltativo)", "") or None
    chesscom_user = _ask("Username Chess.com (facoltativo)", "") or None
    profile.account = Account(lichess=lichess_user, chesscom=chesscom_user)
    profile.repertorio = Repertorio()

    # 4. Provider LLM e chiave (facoltativi) + token Lichess -> secrets.env (chmod 600).
    #    Si chiedono solo token, mai la password di Lichess/Chess.com (§4.4).
    provider = _ask_choice("Provider LLM", ["anthropic", "openrouter"], "anthropic")
    anthropic_key = openrouter_key = None
    if provider == "openrouter":
        openrouter_key = _ask("OpenRouter API key (facoltativa, non verra' mostrata)", "") or None
    else:
        anthropic_key = _ask("Anthropic API key (facoltativa, non verra' mostrata)", "") or None
    lichess_token = _ask("Lichess token (facoltativo, solo per gioco live P3)", "") or None

    # 5. Rilevamento Stockfish (+ Syzygy/Maia se presenti)
    stockfish = find_stockfish()
    if stockfish:
        print(f"\nStockfish rilevato: {stockfish}")
    else:
        print("\nATTENZIONE: Stockfish non trovato.")
        print("  Installa con: apt install stockfish   (oppure imposta GMVIBE_STOCKFISH)")
    syzygy = Path("syzygy")
    print(f"  Syzygy (opzionale): {'presente' if syzygy.exists() else 'non presente'}")
    print("  Maia (opzionale): non integrato in FASE 1 (vedi FASE 8)")

    # Scrittura profilo + segreti
    profile_path = cfg.save_profile(profile)
    print(f"\nProfilo scritto in: {profile_path}")
    if anthropic_key or openrouter_key or lichess_token:
        secrets = Secrets(
            anthropic_api_key=anthropic_key,
            openrouter_api_key=openrouter_key,
            lichess_token=lichess_token,
        )
        secrets_path = cfg.save_secrets(nickname, secrets)
        print(f"Segreti scritti in: {secrets_path} (chmod 600)")
    if provider == "openrouter":
        print("  Per usare OpenRouter imposta `coach.provider: openrouter` in levels.yaml "
              "oppure esporta GMVIBE_COACH_PROVIDER=openrouter.")

    # 6. Test finale: analizza una partita d'esempio inclusa nel repo
    if stockfish and _confirm("\nAnalizzo una partita d'esempio adesso?", default=True):
        _demo_analysis(nickname)
    return 0


def _confirm(prompt: str, default: bool = True) -> bool:
    d = "S/n" if default else "s/N"
    raw = input(f"{prompt} [{d}]: ").strip().lower()
    if not raw:
        return default
    return raw in _YES


def _demo_analysis(nickname: str) -> None:
    from .analyze import analyze_game, render_table

    sample = paths.project_root() / "tests" / "fixtures" / "sample_short.pgn"
    if not sample.exists():
        print("  (partita d'esempio non trovata, salto la demo)")
        return
    from ..sources.pgn_loader import load_pgn

    # Usa la partita d'esempio, con il profilo appena creato.
    text = sample.read_text(encoding="utf-8")
    analysis, stats = analyze_game(text, profile_name=nickname)
    print()
    print(render_table(analysis))
    print(f"\n{stats['moves']} mosse, {stats['engine_calls']} chiamate motore.")
