"""Comando ``analyze``: PGN -> tabella mossa per mossa (documentazione.txt §5, §10).

La logica sta in :mod:`gmvibe.analysis.pipeline`; qui c'e' solo presentazione.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ..analysis.pipeline import analyze_game
from ..fairplay import FairPlayViolation
from ..models import CoachReport, GameAnalysis, LLMUsage, MoveRow

__all__ = ["analyze_game", "render_table", "render_summary", "render_coach", "run"]

_LABEL_SYMBOL = {
    "imprecisione": "?!",
    "errore": "?",
    "svarione": "??",
    "ok": "",
}
_PHASE_SHORT = {"apertura": "ap", "mediogioco": "mg", "finale": "fi"}
_NATURE_SHORT = {
    "tattica_mancata": "tattica",
    "pezzo_in_presa": "in_presa",
    "valutazione_errata": "valutaz",
    "piano_sbagliato": "piano",
    "profilassi_mancata": "profilassi",
    "sicurezza_re": "re",
    "tecnica_conversione": "conversione",
    "tecnica_difesa": "difesa",
    "finale_tecnica": "finale",
    "tempo": "tempo",
    "teoria": "teoria",
}


def _fmt_eval(score_cp: int | None, mate: int | None) -> str:
    if mate is not None:
        return f"M{mate:+d}"
    if score_cp is None:
        return "-"
    return f"{score_cp / 100.0:+.2f}"


def _fmt_clock(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def render_table(analysis: GameAnalysis) -> str:
    header = (
        f"{'#':>4} {'C':<1} {'SAN':<8} {'eval':>7} {'win%':>6} {'Δwin%':>6} "
        f"{'fase':>4} {'cx':>5} {'':<2} {'natura':<11} {'clock':>6}  apertura"
    )
    lines = [header, "-" * len(header)]
    for r in analysis.rows:
        cx = f"{r.complexity.score:.0f}" if r.complexity is not None else ""
        nature = _NATURE_SHORT.get(r.error_label or "", "")
        lines.append(
            f"{r.move_number:>3}{'.' if r.color == 'white' else '…'} "
            f"{r.color[0].upper()} {r.san:<8} {_fmt_eval(r.score_cp, r.mate):>7} "
            f"{(r.winpct_after if r.winpct_after is not None else 0.0):>6.1f} "
            f"{(r.winpct_loss if r.winpct_loss is not None else 0.0):>6.1f} "
            f"{_PHASE_SHORT.get(r.phase or '', ''):>4} {cx:>5} "
            f"{_LABEL_SYMBOL.get(r.label, ''):<2} {nature:<11} "
            f"{_fmt_clock(r.clock_seconds):>6}  {r.opening_name or ''}"
        )
    return "\n".join(lines)


def render_summary(analysis: GameAnalysis) -> str:
    def acc(v: float | None) -> str:
        return f"{v:.1f}" if v is not None else "-"

    out = [
        f"Accuratezza:  bianco {acc(analysis.accuracy.bianco)}  "
        f"nero {acc(analysis.accuracy.nero)}",
    ]
    for phase in ("apertura", "mediogioco", "finale"):
        s = analysis.accuracy_by_phase.get(phase)
        if s is not None:
            out.append(f"  {phase:<11} bianco {acc(s.bianco):>5}  nero {acc(s.nero):>5}")

    def cnt(color: str) -> str:
        c = analysis.counts.get(color, {})
        return (f"?! {c.get('imprecisione', 0)}  ? {c.get('errore', 0)}  "
                f"?? {c.get('svarione', 0)}")

    out.append(f"Errori bianco: {cnt('white')}")
    out.append(f"Errori nero:   {cnt('black')}")

    nature: dict[str, int] = {}
    for r in analysis.rows:
        if r.error_label:
            nature[r.error_label] = nature.get(r.error_label, 0) + 1
    if nature:
        parts = ", ".join(f"{_NATURE_SHORT.get(k, k)} {v}" for k, v in sorted(nature.items()))
        out.append(f"Natura errori: {parts}")

    # --- FASE 4 ---
    if analysis.opening is not None:
        op = analysis.opening
        if op.name is None:
            out.append("Apertura: posizione iniziale non standard (nessuna teoria)")
        else:
            label = op.name + (f" ({op.eco})" if op.eco else "")
            if op.exit_ply is not None:
                out.append(
                    f"Apertura: {label} — uscita dalla teoria a mossa {op.exit_ply} ({op.exit_move_san})"
                )
            else:
                out.append(f"Apertura: {label} — ancora in teoria nel campione analizzato")

    if analysis.endgame:
        verdicts = ", ".join(
            f"{v.ply}: {'cambia' if v.result_changed else 'confermato'}"
            for v in analysis.endgame
        )
        out.append(f"Finale (tablebase): {verdicts}")
    else:
        out.append("Finale (tablebase): nessun finale con pochi pezzi")

    if analysis.time is not None:
        t = analysis.time
        def secs(v):
            return f"{v:.0f}s" if v is not None else "-"
        out.append(
            f"Tempo ({t.cadenza}): medio bianco {secs(t.white_avg)} / nero {secs(t.black_avg)}, "
            f"max bianco {secs(t.white_max)} / nero {secs(t.black_max)}, zeitnot {t.zeitnot_moves}"
        )
        for note in t.notes:
            out.append(f"  · {note.san} ({note.color}): {note.kind} ({secs(note.seconds)})")
    return "\n".join(out)


def render_coach(report: CoachReport, *, usage: LLMUsage | None, meta: dict) -> str:
    """Rende il report del Maestro virtuale in testo (§9.1, solo terminale).

    Il JSON completo resta in ``analysis.coach`` per la UI (FASE 6): qui una resa
    leggibile del verdetto, dei momenti e delle azioni da allenare.
    """
    stato = "testo validato" if meta.get("validated") else "SOLI DATI MOTORE (fallback)"
    out: list[str] = ["", f"Maestro virtuale — {stato}"]
    if usage is not None:
        out.append(
            f"costo: {usage.input_tokens} in / {usage.output_tokens} out token "
            f"({usage.cache_read_input_tokens} da cache) ≈ ${usage.cost_usd:.4f} [{usage.model}]"
        )
    total = meta.get("varianti_totali")
    if total is not None:
        out.append(
            f"varianti legali: {meta.get('varianti_legali', 0)}/{total} "
            f"({meta.get('varianti_legali_pct', 100.0)}%)"
        )
    if meta.get("regenerated"):
        out.append("nota: l'output è stato rigenerato una volta per errori di validazione")

    if report.sintesi_partita:
        out.append("")
        out.append(report.sintesi_partita)

    op = report.apertura
    if op.valutazione or op.deviazione or op.da_ripassare:
        out.append("")
        out.append(f"Apertura: {op.valutazione}".rstrip())
        if op.deviazione:
            out.append(f"  deviazione: {op.deviazione}")
        if op.da_ripassare:
            out.append(f"  da ripassare: {op.da_ripassare}")

    if report.momenti:
        out.append("")
        out.append("Momenti critici:")
        for m in report.momenti:
            played = f" — giocata {m.mossa_giocata}" if m.mossa_giocata else ""
            best = f", migliore {m.mossa_migliore}" if m.mossa_migliore else ""
            out.append(f"  [{m.id}] {m.titolo}{played}{best}")
            if m.spiegazione:
                out.append(f"      {m.spiegazione}")
            if m.idea_del_motore:
                out.append(f"      idea del motore: {m.idea_del_motore}")
            if m.regola_di_pensiero:
                out.append(f"      regola di pensiero: {m.regola_di_pensiero}")
            for v in m.varianti_citate:
                seq = " ".join(v.mosse_san)
                out.append(f"      variante: {seq}" + (f" — {v.commento}" if v.commento else ""))

    if report.gestione_tempo:
        out.append("")
        out.append(f"Gestione del tempo: {report.gestione_tempo}")
    if report.fase_piu_debole:
        out.append(f"Fase più debole: {report.fase_piu_debole}")
    if report.lezione_finale:
        out.append(f"Lezione finale: {report.lezione_finale}")
    if report.da_allenare:
        out.append("Da allenare:")
        out.extend(f"  - {a}" for a in report.da_allenare)
    return "\n".join(out)


def run(args: argparse.Namespace) -> int:
    from .. import config as cfg

    try:
        pgn_text = Path(args.pgn).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Errore: impossibile leggere il file PGN {args.pgn!r}: {exc}", file=sys.stderr)
        return 2
    maia = _maia_client(args)
    t0 = time.perf_counter()
    try:
        if maia is not None:
            with maia:
                analysis, stats = analyze_game(
                    pgn_text, profile_name=args.profile, depth=args.depth,
                    time_limit=args.time, deep_depth=getattr(args, "deep_depth", None),
                    deep=not getattr(args, "no_deep", False), refresh=args.refresh,
                    maia_client=maia,
                )
        else:
            analysis, stats = analyze_game(
                pgn_text,
                profile_name=args.profile,
                depth=args.depth,
                time_limit=args.time,
                deep_depth=getattr(args, "deep_depth", None),
                deep=not getattr(args, "no_deep", False),
                refresh=args.refresh,
            )
    except FairPlayViolation as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    except (ValueError, cfg.ConfigError) as exc:
        # PGN vuoto/illeggibile o profilo/levels non validi: messaggio chiaro, non traceback.
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - t0

    print(f"Partita: {analysis.white or '?'} vs {analysis.black or '?'}  "
          f"Risultato: {analysis.result}")
    if analysis.level_config is not None:
        print(f"Calibrazione: {analysis.level_config.calibration_line}")
    print()
    print(render_table(analysis))
    print()
    print(render_summary(analysis))
    print()
    skipped = stats.get("deep_skipped", 0)
    extra = f" (+{skipped} non approfonditi: limite max_candidates)" if skipped else ""
    print(
        f"{stats['moves']} mosse | candidati analizzati: {stats['deep_analyses'] // 2}{extra} | "
        f"cache hit {stats['cache_hits']} | chiamate motore {stats['engine_calls']} | "
        f"{elapsed:.2f}s"
    )

    if getattr(args, "save", False):
        _save(args, analysis, pgn_text)

    if getattr(args, "coach", False):
        _run_coach(args, analysis)
    return 0


def _maia_client(args: argparse.Namespace):
    """Costruisce il client Maia (§5.H, FASE 8) se ``--maia``; altrimenti None.

    Maia e' OPZIONALE: se Lc0 o i pesi mancano si avvisa e si prosegue senza fattore 5.
    Il kind/il modello del livello vengono da ``resolve_level`` (CLAUDE.md).
    """
    if not getattr(args, "maia", False):
        return None
    from .. import config as cfg
    from ..engine.maia import build_maia_client, maia_availability
    from ..level import resolve_level

    levels = cfg.load_levels()
    if not args.profile:
        print("[maia] serve --profile per risolvere il livello: fattore 5 disattivato",
              file=sys.stderr)
        return None
    profile = cfg.load_profile(args.profile)
    conversion = cfg.load_rating_conversion()
    m2, m1 = maia_availability(levels.maia)
    lc = resolve_level(profile, levels, conversion, maia2_available=m2, maia1_available=m1)
    client = build_maia_client(
        enabled=levels.maia.enabled, maia_kind=lc.maia_kind,
        maia_model=lc.maia_model, config=levels.maia,
    )
    if client is None:
        print("[maia] Lc0/pesi Maia non disponibili: Complexity Score senza fattore 5",
              file=sys.stderr)
    return client


def _save(args: argparse.Namespace, analysis: GameAnalysis, pgn_text: str) -> None:
    """Persiste analisi, errori etichettati e puzzle (§7) quando richiesto con --save."""
    from .. import config as cfg
    from ..analysis.store import persist_analysis
    from ..db import Database

    user_id = args.profile or "default"
    db = Database()
    try:
        result = persist_analysis(
            db, user_id, pgn_text=pgn_text, analysis=analysis, levels=cfg.load_levels(),
            source="pgn", nickname=args.profile,
        )
        verb = "Aggiornato" if result.reused else "Salvato"
        print(
            f"\n{verb} (analisi #{result.analysis_id}): {result.error_count} errori "
            f"etichettati, {result.puzzle_count} puzzle."
        )
    finally:
        db.close()


def _run_coach(args: argparse.Namespace, analysis: GameAnalysis) -> None:
    """Esegue il layer LLM (§6) e ne stampa il report. Errori non fatali."""
    from .. import config as cfg
    from ..ai_coach.coach import CoachError, coach_analysis

    profile = None
    if args.profile is not None:
        try:
            profile = cfg.load_profile(args.profile)
        except cfg.ConfigError as exc:
            print(f"\n[coach] profilo non caricato: {exc}", file=sys.stderr)

    try:
        coach_cfg = cfg.load_levels().coach
    except cfg.ConfigError:
        coach_cfg = None

    try:
        result = coach_analysis(analysis, profile=profile, config=coach_cfg)
    except CoachError as exc:
        # Nessuna chiave/API/rete: l'analisi del motore resta comunque mostrata.
        print(f"\n[coach] non disponibile: {exc}", file=sys.stderr)
        return

    print(render_coach(result.report, usage=result.usage, meta=result.meta))
