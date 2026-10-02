"""Comando ``profile``: profilo di debolezze cross-partita (documentazione.txt §7)."""

from __future__ import annotations

import argparse

from .. import config as cfg
from ..db import Database
from ..models import WeaknessProfile


def _fmt(v: float | None) -> str:
    return f"{v:.1f}" if v is not None else "-"


def render_profile(p: WeaknessProfile) -> str:
    out: list[str] = [
        f"Profilo di debolezze — utente {p.user_id}, ultime {p.window} partite "
        f"({p.games_analyzed} analizzate)",
    ]
    if p.games_analyzed == 0:
        out.append("Nessuna analisi disponibile: usa `python -m gmvibe import ...`.")
        return "\n".join(out)

    out.append("")
    out.append("Etichette più frequenti (§7):")
    for lf in p.label_frequency[:8]:
        extra = []
        if lf.phase:
            extra.append(lf.phase)
        if lf.clock_bucket:
            extra.append(f"clock {lf.clock_bucket}")
        suffix = f"  [{', '.join(extra)}]" if extra else ""
        out.append(f"  {lf.error_label:<20} {lf.games} su {p.games_analyzed} partite "
                   f"({lf.occurrences} occorrenze){suffix}")

    out.append("")
    if p.accuracy_by_phase:
        out.append("Accuratezza per fase (media):")
        for ph in p.accuracy_by_phase:
            out.append(f"  {ph.fase:<11} bianco {_fmt(ph.bianco):>5}  nero {_fmt(ph.nero):>5}  "
                       f"media {_fmt(ph.media):>5}")
    out.append(f"Accuratezza per colore: bianco {_fmt(p.accuracy_by_color.bianco)} / "
               f"nero {_fmt(p.accuracy_by_color.nero)}")

    if p.accuracy_by_cadenza:
        out.append("Accuratezza per cadenza:")
        for c in p.accuracy_by_cadenza:
            out.append(f"  {c.cadenza:<9} {c.games} partite  bianco {_fmt(c.bianco)} / "
                       f"nero {_fmt(c.nero)}")

    if p.clock_buckets:
        out.append("")
        out.append("Errori in funzione del clock:")
        for b in p.clock_buckets:
            out.append(f"  {b.label:<10} {b.errors}/{b.moves} mosse "
                       f"({b.error_rate * 100:.0f}% di errore)")

    if p.repertoire:
        out.append("")
        out.append("Repertorio (aperture):")
        for o in p.repertoire[:8]:
            eco = f" {o.eco}" if o.eco else ""
            out.append(f"  {o.opening}{eco}: {o.games} partite  "
                       f"+{o.wins} ={o.draws} -{o.losses}  acc {_fmt(o.accuracy)}")

    if len(p.evolution) > 1:
        out.append("")
        first = next((e.accuracy for e in p.evolution if e.accuracy is not None), None)
        last = next((e.accuracy for e in reversed(p.evolution) if e.accuracy is not None), None)
        out.append(f"Evoluzione: {len(p.evolution)} partite, accuratezza "
                   f"{_fmt(first)} → {_fmt(last)}; tendenza finali: {p.trend_finali}")

    if p.da_allenare:
        out.append("")
        out.append("Da allenare (§9.1):")
        out.extend(f"  - {a}" for a in p.da_allenare)
    return "\n".join(out)


def run(args: argparse.Namespace) -> int:
    from ..profile.weaknesses import build_weakness_profile

    levels = cfg.load_levels()
    user_id = args.profile or "default"
    db = Database()
    try:
        profile = build_weakness_profile(
            db, user_id, window=args.last, levels=levels, nickname=args.profile
        )
        print(render_profile(profile))
    finally:
        db.close()
    return 0
