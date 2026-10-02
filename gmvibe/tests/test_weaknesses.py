"""Test del profilo di debolezze cross-partita (documentazione.txt §7).

Costruisce un DB con analisi ed errori sintetici e verifica le aggregazioni: etichette più
frequenti ("X su N partite"), accuratezza per fase/colore/cadenza, errori vs clock,
repertorio, evoluzione e azioni "da allenare". Offline, deterministico.
"""

from __future__ import annotations

import json

from gmvibe import config as cfg
from gmvibe.profile.weaknesses import build_weakness_profile, recurrence_by_label


def _save(db, user_id, *, white, black, result, accuracy, by_phase, opening, cadenza, rows):
    analysis = {
        "rows": rows, "accuracy": accuracy, "accuracy_by_phase": by_phase,
        "opening": opening, "time": {"cadenza": cadenza}, "white": white, "black": black,
        "result": result,
    }
    return db.save_analysis(
        user_id, f"hash-{white}-{result}-{opening.get('name')}", white=white, black=black,
        result=result, analysis_json=json.dumps(analysis),
    )


def _row(ply, label, clock_delta, error_label=None):
    return {"ply": ply, "label": label, "clock_delta": clock_delta,
            "error_label": error_label, "color": "white"}


def _seed(db, user_id="t1900", games=20):
    """20 partite: 7 con tecnica_conversione, accuratezza dei finali in crescita."""
    gids = []
    for i in range(games):
        finale = 60 + i  # 60..79: migliora nel tempo
        gid = _save(
            db, user_id,
            white="t1900" if i % 2 == 0 else "avversario",
            black="avversario" if i % 2 == 0 else "t1900",
            result="1-0" if i % 3 == 0 else ("0-1" if i % 3 == 1 else "1/2-1/2"),
            accuracy={"bianco": 80.0 + (i % 5), "nero": 75.0 + (i % 4)},
            by_phase={
                "apertura": {"bianco": 95.0, "nero": 93.0},
                "mediogioco": {"bianco": 82.0, "nero": 80.0},
                "finale": {"bianco": float(finale), "nero": float(finale - 5)},
            },
            opening={"name": "Italian Game" if i < 12 else "Sicilian Defense", "eco": "C50"},
            cadenza="blitz" if i < 15 else "rapid",
            rows=[
                _row(1, "ok", 4.0),
                _row(3, "errore", 5.0, "tempo"),      # <10s
                _row(5, "svarione", 40.0, "tattica_mancata"),  # 30-120s
            ],
        )
        gids.append(gid)
        # Gli errori etichettati rispecchiano le righe non-"ok" (come fa persist_analysis).
        errors = [
            {"ply": 3, "move_number": 2, "color": "white", "phase": "mediogioco",
             "label": "errore", "error_label": "tempo", "san": "Nf3", "fen_before": "f",
             "opening_name": "Italian Game", "opening_eco": "C50", "cadenza": "blitz",
             "clock_seconds": 5.0, "winpct_loss": 12.0},
            {"ply": 5, "move_number": 3, "color": "white", "phase": "mediogioco",
             "label": "svarione", "error_label": "tattica_mancata", "san": "Bc4",
             "fen_before": "f", "opening_name": "Italian Game", "opening_eco": "C50",
             "cadenza": "blitz", "clock_seconds": 40.0, "winpct_loss": 25.0},
        ]
        if i < 7:  # tecnica_conversione in 7 partite su 20
            errors.append({"ply": 9, "move_number": 5, "color": "white", "phase": "finale",
                           "label": "errore", "error_label": "tecnica_conversione",
                           "san": "Kb7", "fen_before": "f", "opening_name": "Italian Game",
                           "opening_eco": "C50", "cadenza": "blitz", "clock_seconds": 60.0,
                           "winpct_loss": 20.0})
        db.save_errors(user_id, gid, errors)
    return gids


def test_label_frequency_counts_games(tmp_db):
    _seed(tmp_db)
    p = build_weakness_profile(tmp_db, "t1900", levels=cfg.load_levels(), nickname="t1900")
    assert p.games_analyzed == 20
    top = {lf.error_label: lf for lf in p.label_frequency}
    assert top["tecnica_conversione"].games == 7
    assert top["tecnica_conversione"].occurrences == 7
    # "7 su 20 partite" compare nelle azioni da allenare
    assert any("7 su 20 partite" in a for a in p.da_allenare)
    assert len(p.da_allenare) == 3


def test_accuracy_by_phase_and_color(tmp_db):
    _seed(tmp_db)
    p = build_weakness_profile(tmp_db, "t1900", levels=cfg.load_levels(), nickname="t1900")
    phases = {x.fase: x for x in p.accuracy_by_phase}
    assert phases["apertura"].media == 94.0
    assert phases["finale"].media is not None
    # con nickname noto, l'accuratezza per colore è quella del giocatore
    assert p.accuracy_by_color.bianco == p.accuracy_by_color.nero


def test_accuracy_by_cadenza(tmp_db):
    _seed(tmp_db)
    p = build_weakness_profile(tmp_db, "t1900", levels=cfg.load_levels())
    cad = {c.cadenza: c for c in p.accuracy_by_cadenza}
    assert cad["blitz"].games == 15 and cad["rapid"].games == 5


def test_clock_buckets_error_rate(tmp_db):
    _seed(tmp_db)
    p = build_weakness_profile(tmp_db, "t1900", levels=cfg.load_levels())
    buckets = {b.label: b for b in p.clock_buckets}
    # fascia "30-120s": una mossa, un errore (svarione)
    assert buckets["30-120s"].moves == 20 and buckets["30-120s"].errors == 20
    assert buckets["30-120s"].error_rate == 1.0
    assert buckets["<10s"].moves == 40  # due mosse con clock_delta 4s e 5s per partita


def test_repertoire_lists_losing_lines(tmp_db):
    _seed(tmp_db)
    p = build_weakness_profile(tmp_db, "t1900", levels=cfg.load_levels(), nickname="t1900")
    rep = {o.opening: o for o in p.repertoire}
    assert rep["Italian Game"].games == 12 and rep["Sicilian Defense"].games == 8
    assert rep["Italian Game"].wins + rep["Italian Game"].draws + rep["Italian Game"].losses == 12


def test_evolution_trend_improving(tmp_db):
    _seed(tmp_db)
    p = build_weakness_profile(tmp_db, "t1900", levels=cfg.load_levels(), nickname="t1900")
    assert len(p.evolution) == 20
    # i finali passano da 60 a 79 -> migliorando
    assert p.trend_finali == "migliorando"


def test_window_limits_games(tmp_db):
    _seed(tmp_db)
    p = build_weakness_profile(tmp_db, "t1900", window=5, levels=cfg.load_levels())
    assert p.games_analyzed == 5


def test_empty_profile(tmp_db):
    p = build_weakness_profile(tmp_db, "nessuno", levels=cfg.load_levels())
    assert p.games_analyzed == 0
    assert p.label_frequency == [] and p.da_allenare == []


def test_recurrence_counts_previous_games(tmp_db):
    gids = _seed(tmp_db, games=5)
    # prima dell'ultima partita ci sono 4 partite precedenti con tecnica_conversione
    assert recurrence_by_label(tmp_db, "t1900", gids[-1], ["tecnica_conversione"]) == {
        "tecnica_conversione": 4
    }
    # prima della prima partita: nessuna ricorrenza
    assert recurrence_by_label(tmp_db, "t1900", gids[0], ["tecnica_conversione"]) == {
        "tecnica_conversione": 0
    }
