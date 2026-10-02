"""Test obbligatori di ``resolve_level`` (documentazione.txt §4.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gmvibe.level import resolve_level, to_lichess_scale
from gmvibe.models import PlayerProfile


def _profile(rating, tipo="fide", cadenza="blitz"):
    return PlayerProfile(nickname="t", rating=rating, rating_tipo=tipo, cadenza_preferita=cadenza)


# --------------------------------------------------------------------------- #
# Monotonia: a rating piu' alto le soglie di errore NON diventano piu' larghe.
# --------------------------------------------------------------------------- #
def test_monotonicity(levels, conversion):
    ratings = list(range(1200, 2951, 50))
    prev = None
    for r in ratings:
        lc = resolve_level(_profile(r), levels, conversion)
        if prev is not None:
            assert lc.error_thresholds.imprecisione <= prev.error_thresholds.imprecisione + 1e-9
            assert lc.error_thresholds.errore <= prev.error_thresholds.errore + 1e-9
            assert lc.error_thresholds.svarione <= prev.error_thresholds.svarione + 1e-9
            assert lc.critical_moments <= prev.critical_moments
        prev = lc


# --------------------------------------------------------------------------- #
# Casi limite
# --------------------------------------------------------------------------- #
def test_edge_1100(levels, conversion):
    lc = resolve_level(_profile(1100), levels, conversion)
    # Sotto il primo anchor (1400): clamp ai valori di 1400 + extrapolated
    anchor0 = levels.sorted_anchors()[0]
    assert lc.extrapolated is True
    assert lc.error_thresholds.imprecisione == pytest.approx(anchor0.error_thresholds.imprecisione)


def test_edge_1900(levels, conversion):
    lc = resolve_level(_profile(1900), levels, conversion)
    assert lc.extrapolated is False
    assert lc.error_thresholds.errore > 0


def test_edge_2200(levels, conversion):
    lc1900 = resolve_level(_profile(1900), levels, conversion)
    lc2200 = resolve_level(_profile(2200), levels, conversion)
    # 2200 e' interpolato tra 2000 e 2300: diverso da 1900
    assert lc2200.error_thresholds.errore != lc1900.error_thresholds.errore
    assert lc2200.error_thresholds.errore < lc1900.error_thresholds.errore


def test_edge_2600(levels, conversion):
    lc = resolve_level(_profile(2600), levels, conversion)
    assert lc.extrapolated is True
    top = levels.sorted_anchors()[-1]
    assert lc.error_thresholds.errore == pytest.approx(top.error_thresholds.errore)


def test_edge_3000(levels, conversion):
    lc = resolve_level(_profile(3000), levels, conversion)
    assert lc.extrapolated is True
    assert lc.critical_moments >= 1


def test_invalid_rating(levels, conversion):
    with pytest.raises(ValidationError):
        PlayerProfile(nickname="t", rating=-5)
    with pytest.raises(ValidationError):
        PlayerProfile(nickname="t", rating="abc")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        PlayerProfile(nickname="t", rating=99999)


# --------------------------------------------------------------------------- #
# Determinismo
# --------------------------------------------------------------------------- #
def test_determinism(levels, conversion):
    p = _profile(1950)
    a = resolve_level(p, levels, conversion)
    b = resolve_level(p, levels, conversion)
    assert a == b
    assert a.calibration_line == b.calibration_line


# --------------------------------------------------------------------------- #
# Priorita' del rating Lichess reale sulla conversione (§4.3.1)
# --------------------------------------------------------------------------- #
def test_lichess_priority(levels, conversion):
    p = _profile(1900, tipo="lichess")
    lc = resolve_level(p, levels, conversion, declared_lichess_rating=2050)
    assert lc.rating_lichess_source == "declared_lichess"
    assert lc.estimated is False
    assert lc.rating_lichess == 2050


def test_lichess_declared_without_explicit_flag(levels, conversion):
    p = _profile(1900, tipo="lichess")
    lc = resolve_level(p, levels, conversion)
    assert lc.rating_lichess_source == "declared_lichess"
    assert lc.estimated is False


# --------------------------------------------------------------------------- #
# Flag estimated / extrapolated
# --------------------------------------------------------------------------- #
def test_estimated_flag(levels, conversion):
    assert resolve_level(_profile(1900, tipo="fide"), levels, conversion).estimated is True
    assert resolve_level(_profile(1900, tipo="lichess"), levels, conversion).estimated is False


def test_extrapolated_flag(levels, conversion):
    # tipo "altro" ha delta 0: il rating coincide con la scala Lichess, cosi' il flag
    # riflette SOLO il clamp sull'intervallo degli anchor (non la conversione).
    for r in (1100, 2600, 3000):
        assert resolve_level(_profile(r, tipo="altro"), levels, conversion).extrapolated is True
    for r in (1900, 2200):
        assert resolve_level(_profile(r, tipo="altro"), levels, conversion).extrapolated is False


def test_conversion_can_push_out_of_range(levels, conversion):
    # FIDE 2200 blitz -> +120 = 2320, oltre l'anchor massimo -> extrapolated
    lc = resolve_level(_profile(2200, tipo="fide", cadenza="blitz"), levels, conversion)
    assert lc.rating_lichess == 2320
    assert lc.extrapolated is True


def test_conversion_applies_delta(levels, conversion):
    fide = resolve_level(_profile(2000, tipo="fide", cadenza="rapid"), levels, conversion)
    altro = resolve_level(_profile(2000, tipo="altro", cadenza="rapid"), levels, conversion)
    # fide ha un delta positivo rispetto a "altro" (0)
    assert fide.rating_lichess > altro.rating_lichess


def test_conversion_function():
    # "lichess" non converte; "altro" ha delta 0 ma resta estimated
    from gmvibe.models import RatingConversion, CadenzaDelta

    conv = RatingConversion(
        tables={"altro": {"rapid": CadenzaDelta(delta=0)}},
    )
    assert to_lichess_scale(1800, "lichess", "rapid", conv) == (1800, False)
    assert to_lichess_scale(1800, "altro", "rapid", conv) == (1800, True)


# --------------------------------------------------------------------------- #
# Criterio di accettazione §10: 1900 vs 2200 -> LevelConfig diversi
# --------------------------------------------------------------------------- #
def test_two_profiles_differ(levels, conversion, profile_1900, profile_2200):
    a = resolve_level(profile_1900, levels, conversion)
    b = resolve_level(profile_2200, levels, conversion)
    assert a != b
    assert a.error_thresholds.errore != b.error_thresholds.errore
