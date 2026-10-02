"""Test dell'analisi del tempo (documentazione.txt §5.J)."""

from __future__ import annotations

from gmvibe.analysis.time_analysis import analyze_time, cadenza_from_timecontrol, profile_for
from gmvibe.models import MoveRow, TimeAnalysisConfig, TimeProfile

CFG = TimeAnalysisConfig(profiles={
    "blitz": TimeProfile(zeitnot_seconds=15.0, banal_time_seconds=60.0, critical_fast_seconds=5.0),
    "classica": TimeProfile(zeitnot_seconds=None, banal_time_seconds=300.0, critical_fast_seconds=30.0),
})


def _row(ply, color, clock_seconds=None, clock_delta=None, candidate=False, san="e4"):
    return MoveRow(
        ply=ply, move_number=(ply + 1) // 2, color=color, san=san, uci="e2e4",
        fen_before="8/8/8/8/8/8/8/4K2k w - - 0 1", fen_after="8/8/8/8/8/8/8/4K2k w - - 0 1",
        clock_seconds=clock_seconds, clock_delta=clock_delta, is_candidate=candidate,
    )


def test_cadenza_from_timecontrol():
    assert cadenza_from_timecontrol("300+0") == "blitz"
    assert cadenza_from_timecontrol("180+2") == "blitz"      # 180+80=260 <600
    assert cadenza_from_timecontrol("600+5") == "rapid"      # 600+200=800
    assert cadenza_from_timecontrol("90m+30") == "blitz"     # formato non in secondi: fallback
    assert cadenza_from_timecontrol(None) == "blitz"
    assert cadenza_from_timecontrol("5400+30") == "classica" # 5400+1200=6600


def test_profile_lookup():
    assert profile_for("blitz", CFG).zeitnot_seconds == 15.0
    assert profile_for("classica", CFG).zeitnot_seconds is None
    # cadenza sconosciuta -> primo profilo disponibile
    assert profile_for("sconosciuta", CFG) is not None


def test_zeitnot_detected_in_blitz():
    rows = [_row(1, "white", clock_seconds=10.0), _row(2, "black", clock_seconds=200.0)]
    summary = analyze_time(rows, "blitz", CFG)
    assert summary.zeitnot_moves == 1
    assert any(n.kind == "zeitnot" for n in summary.notes)


def test_no_zeitnot_in_classica():
    # in classica zeitnot e' disattivato anche con clock basso
    rows = [_row(1, "white", clock_seconds=5.0)]
    summary = analyze_time(rows, "classica", CFG)
    assert summary.zeitnot_moves == 0


def test_banal_slow_move():
    rows = [_row(1, "white", clock_delta=90.0, candidate=False)]
    summary = analyze_time(rows, "blitz", CFG)
    assert any(n.kind == "banale_lenta" for n in summary.notes)


def test_critical_fast_move():
    rows = [_row(1, "white", clock_delta=2.0, candidate=True)]
    summary = analyze_time(rows, "blitz", CFG)
    assert any(n.kind == "critica_veloce" for n in summary.notes)


def test_averages_and_max():
    rows = [
        _row(1, "white", clock_delta=10.0),
        _row(2, "black", clock_delta=20.0),
        _row(3, "white", clock_delta=30.0),
    ]
    summary = analyze_time(rows, "blitz", CFG)
    assert summary.white_avg == 20.0
    assert summary.black_avg == 20.0
    assert summary.white_max == 30.0
