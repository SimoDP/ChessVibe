"""Profilo di debolezze cross-partita (documentazione.txt §7).

Una singola partita insegna poco; i pattern insegnano molto. Questo modulo aggrega le
ultime N analisi e gli errori etichettati in SQLite e produce un
:class:`~gmvibe.models.WeaknessProfile`:

- etichette più frequenti ("tecnica_conversione: 7 su 20 partite");
- accuratezza per fase / per colore / per cadenza / per apertura;
- errori in funzione del clock;
- repertorio ("quali linee ti fanno perdere");
- evoluzione nel tempo (stai migliorando nei finali?).

Tutto è deterministico e deriva da dati già misurati dal motore: nessun ricalcolo.
"""

from __future__ import annotations

import json

from ..models import (
    AccuracySummary,
    CadenzaAccuracy,
    ClockBucket,
    EvolutionPoint,
    LabelFrequency,
    LevelsConfig,
    OpeningStat,
    PhaseAccuracy,
    ProfileConfig,
    WeaknessProfile,
)

_PHASES = ("apertura", "mediogioco", "finale")

# Nomi leggibili per le azioni "da allenare" (§9.1 punto 9).
_LABEL_TEXT = {
    "tattica_mancata": "tattica (mosse forzanti non viste)",
    "pezzo_in_presa": "pezzi lasciati in presa",
    "valutazione_errata": "valutazione della posizione",
    "piano_sbagliato": "scelta del piano",
    "profilassi_mancata": "profilassi",
    "sicurezza_re": "sicurezza del Re",
    "tecnica_conversione": "conversione delle posizioni vinte",
    "tecnica_difesa": "difesa nelle posizioni difficili",
    "finale_tecnica": "tecnica dei finali",
    "tempo": "gestione del tempo",
    "teoria": "repertorio di apertura",
}


def _mean(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _bucket_label(seconds: float, buckets: list[float]) -> str:
    edges = sorted(buckets)
    prev = None
    for edge in edges:
        if seconds < edge:
            return f"<{edge:g}s" if prev is None else f"{prev:g}-{edge:g}s"
        prev = edge
    return f">{edges[-1]:g}s" if edges else "n/d"


def _player_color(white: str | None, black: str | None, nickname: str | None) -> str | None:
    if not nickname:
        return None
    nick = nickname.strip().lower()
    if (white or "").strip().lower() == nick:
        return "white"
    if (black or "").strip().lower() == nick:
        return "black"
    return None


def _result_pov(result: str | None, color: str | None) -> str | None:
    """Esito ("win"/"draw"/"loss") dal punto di vista di ``color`` (default: Bianco)."""
    if result not in ("1-0", "0-1", "1/2-1/2"):
        return None
    if result == "1/2-1/2":
        return "draw"
    white_won = result == "1-0"
    if color == "black":
        return "win" if not white_won else "loss"
    return "win" if white_won else "loss"


def recurrence_by_label(db, user_id: str, game_id: int, labels: list[str]) -> dict[str, int]:
    """Per ogni etichetta: n. di partite precedenti con lo stesso pattern (§7).

    Da chiamare **prima** di inserire gli errori della partita corrente, così il conteggio
    esclude la partita stessa.
    """
    unique = {lb for lb in labels if lb}
    return {lb: db.error_recurrence(user_id, lb, game_id) for lb in unique}


def build_weakness_profile(
    db,
    user_id: str,
    *,
    window: int | None = None,
    levels: LevelsConfig | None = None,
    nickname: str | None = None,
) -> WeaknessProfile:
    """Costruisce il profilo di debolezze sulle ultime ``window`` partite (§7).

    Args:
        db: :class:`~gmvibe.db.Database`.
        user_id: utente di cui aggregare i dati.
        window: finestra (default: ``levels.profile.window_games``).
        levels: configurazione (soglie di bucketing/h finestra).
        nickname: nickname del giocatore, per attribuire l'esito dei reperti/le aperture
            al lato giusto; se assente si usa il punto di vista del Bianco.
    """
    cfg: ProfileConfig = levels.profile if levels is not None else ProfileConfig()
    window = window or cfg.window_games

    summaries = db.list_analyses(user_id)[:window]
    analyses = []
    for s in summaries:
        raw = db.analysis_json(s["id"])
        if raw is None:
            continue
        try:
            analyses.append((s, json.loads(raw)))
        except json.JSONDecodeError:  # pragma: no cover - JSON sempre scritto da noi
            continue

    errors = db.list_errors(user_id, last_games=window)

    profile = WeaknessProfile(user_id=user_id, window=window, games_analyzed=len(analyses))
    profile.label_frequency = _label_frequency(errors, len(analyses), cfg.clock_buckets_seconds)
    profile.accuracy_by_phase = _accuracy_by_phase(analyses)
    profile.accuracy_by_color = _accuracy_by_color(analyses, nickname)
    profile.accuracy_by_cadenza = _accuracy_by_cadenza(analyses)
    profile.clock_buckets = _clock_buckets(analyses, cfg.clock_buckets_seconds)
    profile.repertoire = _repertoire(analyses, nickname, cfg.min_games_opening)
    profile.evolution, profile.trend_finali = _evolution(analyses, nickname)
    profile.da_allenare = _da_allenare(profile.label_frequency, len(analyses))
    return profile


# --------------------------------------------------------------------------- #
def _label_frequency(
    errors: list[dict], games: int, buckets: list[float]
) -> list[LabelFrequency]:
    by_label: dict[str, dict] = {}
    for e in errors:
        label = e.get("error_label")
        if not label:
            continue
        agg = by_label.setdefault(
            label, {"games": set(), "occurrences": 0, "phases": {}, "buckets": {}}
        )
        agg["occurrences"] += 1
        if e.get("game_id") is not None:
            agg["games"].add(e["game_id"])
        if e.get("phase"):
            agg["phases"][e["phase"]] = agg["phases"].get(e["phase"], 0) + 1
        secs = e.get("clock_seconds")
        if secs is not None:
            bucket = _bucket_label(float(secs), buckets)
            agg["buckets"][bucket] = agg["buckets"].get(bucket, 0) + 1

    out = [
        LabelFrequency(
            error_label=label,
            games=len(a["games"]),
            occurrences=a["occurrences"],
            phase=max(a["phases"], key=a["phases"].get) if a["phases"] else None,
            clock_bucket=max(a["buckets"], key=a["buckets"].get) if a["buckets"] else None,
        )
        for label, a in by_label.items()
    ]
    out.sort(key=lambda x: (-x.games, -x.occurrences, x.error_label))
    return out


def _accuracy_by_phase(analyses: list[tuple[dict, dict]]) -> list[PhaseAccuracy]:
    acc: dict[str, dict[str, list[float | None]]] = {
        ph: {"bianco": [], "nero": []} for ph in _PHASES
    }
    for _s, a in analyses:
        by_phase = a.get("accuracy_by_phase") or {}
        for ph in _PHASES:
            entry = by_phase.get(ph)
            if not entry:
                continue
            acc[ph]["bianco"].append(entry.get("bianco"))
            acc[ph]["nero"].append(entry.get("nero"))

    out: list[PhaseAccuracy] = []
    for ph in _PHASES:
        b, n = _mean(acc[ph]["bianco"]), _mean(acc[ph]["nero"])
        if b is None and n is None:
            continue
        out.append(PhaseAccuracy(fase=ph, bianco=b, nero=n, media=_mean([b, n])))
    return out


def _accuracy_by_color(analyses: list[tuple[dict, dict]], nickname: str | None) -> AccuracySummary:
    """Accuratezza media del giocatore (se noto il lato) o di entrambi i lati."""
    white: list[float | None] = []
    black: list[float | None] = []
    player: list[float | None] = []
    for _s, a in analyses:
        acc = a.get("accuracy") or {}
        white.append(acc.get("bianco"))
        black.append(acc.get("nero"))
        color = _player_color(a.get("white"), a.get("black"), nickname)
        if color == "white":
            player.append(acc.get("bianco"))
        elif color == "black":
            player.append(acc.get("nero"))
    if player:
        m = _mean(player)
        return AccuracySummary(bianco=m, nero=m)
    return AccuracySummary(bianco=_mean(white), nero=_mean(black))


def _accuracy_by_cadenza(analyses: list[tuple[dict, dict]]) -> list[CadenzaAccuracy]:
    groups: dict[str, dict] = {}
    for _s, a in analyses:
        cadenza = ((a.get("time") or {}).get("cadenza")) or "blitz"
        g = groups.setdefault(cadenza, {"games": 0, "bianco": [], "nero": []})
        g["games"] += 1
        acc = a.get("accuracy") or {}
        g["bianco"].append(acc.get("bianco"))
        g["nero"].append(acc.get("nero"))
    out = [
        CadenzaAccuracy(cadenza=c, games=g["games"],
                        bianco=_mean(g["bianco"]), nero=_mean(g["nero"]))
        for c, g in groups.items()
    ]
    out.sort(key=lambda x: -x.games)
    return out


def _clock_buckets(analyses: list[tuple[dict, dict]], buckets: list[float]) -> list[ClockBucket]:
    """Tasso d'errore per fascia di tempo speso sulla mossa (§7)."""
    agg: dict[str, dict] = {}
    for _s, a in analyses:
        for row in a.get("rows") or []:
            secs = row.get("clock_delta")
            if secs is None:
                continue
            bucket = _bucket_label(float(secs), buckets)
            g = agg.setdefault(bucket, {"moves": 0, "errors": 0})
            g["moves"] += 1
            if row.get("label") in ("imprecisione", "errore", "svarione"):
                g["errors"] += 1
    out = [
        ClockBucket(label=b, moves=g["moves"], errors=g["errors"],
                    error_rate=(g["errors"] / g["moves"]) if g["moves"] else 0.0)
        for b, g in agg.items()
    ]
    out.sort(key=lambda x: -x.moves)
    return out


def _repertoire(
    analyses: list[tuple[dict, dict]], nickname: str | None, min_games: int
) -> list[OpeningStat]:
    """Statistiche per apertura: "quali linee ti fanno perdere" (§7)."""
    groups: dict[str, dict] = {}
    for _s, a in analyses:
        opening = a.get("opening") or {}
        name = opening.get("name")
        if not name:
            continue
        g = groups.setdefault(name, {"eco": opening.get("eco"), "games": 0,
                                     "wins": 0, "draws": 0, "losses": 0, "acc": []})
        g["games"] += 1
        color = _player_color(a.get("white"), a.get("black"), nickname)
        res = _result_pov(a.get("result"), color)
        if res == "win":
            g["wins"] += 1
        elif res == "loss":
            g["losses"] += 1
        elif res == "draw":
            g["draws"] += 1
        acc = a.get("accuracy") or {}
        g["acc"].append(acc.get("nero") if color == "black" else acc.get("bianco"))

    out = [
        OpeningStat(opening=name, eco=g["eco"], games=g["games"], wins=g["wins"],
                    draws=g["draws"], losses=g["losses"], accuracy=_mean(g["acc"]))
        for name, g in groups.items()
        if g["games"] >= min_games
    ]
    # Le linee che fanno perdere prima: più sconfitte, poi meno accuratezza.
    out.sort(key=lambda x: (-x.losses, x.accuracy if x.accuracy is not None else 100.0))
    return out


def _evolution(
    analyses: list[tuple[dict, dict]], nickname: str | None
) -> tuple[list[EvolutionPoint], str]:
    points: list[EvolutionPoint] = []
    for s, a in reversed(analyses):  # cronologico: dalla più vecchia alla più recente
        color = _player_color(a.get("white"), a.get("black"), nickname)
        acc = a.get("accuracy") or {}
        by_phase = a.get("accuracy_by_phase") or {}
        finale = (by_phase.get("finale") or {})

        def pick(entry, key):
            if color == "black":
                return entry.get("nero")
            if color == "white":
                return entry.get("bianco")
            return _mean([entry.get("bianco"), entry.get("nero")])

        points.append(EvolutionPoint(
            game_id=int(s["id"]),
            date=s.get("created_at") or "",
            accuracy=pick(acc, "accuracy"),
            finale_accuracy=pick(finale, "finale") if finale else None,
        ))

    finales = [p.finale_accuracy for p in points if p.finale_accuracy is not None]
    return points, _trend(finales)


def _trend(values: list[float]) -> str:
    """Confronta prima e seconda metà della serie: migliorando/stabile/peggiorando."""
    if len(values) < 4:
        return "dati_insufficienti"
    half = len(values) // 2
    first, second = _mean(values[:half]), _mean(values[half:])
    if first is None or second is None:  # pragma: no cover - liste non vuote qui
        return "dati_insufficienti"
    delta = second - first
    if delta > 3.0:
        return "migliorando"
    if delta < -3.0:
        return "peggiorando"
    return "stabile"


def _da_allenare(label_frequency: list[LabelFrequency], games: int) -> list[str]:
    """3 azioni concrete dalle etichette più frequenti (§9.1 punto 9)."""
    out: list[str] = []
    for lf in label_frequency[:3]:
        text = _LABEL_TEXT.get(lf.error_label, lf.error_label)
        detail = f"{lf.games} su {games} partite" if games else f"{lf.occurrences} occorrenze"
        if lf.clock_bucket:
            detail += f", tipicamente {lf.clock_bucket}"
        out.append(f"Allenare {text} ({detail})")
    return out
