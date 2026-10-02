"""Test della formula win% (documentazione.txt §5.E)."""

from __future__ import annotations

import math

import pytest

from gmvibe.metrics import accuracy_from_loss, winpct, winpct_from_score, winpct_loss


def test_zero_is_fifty():
    assert winpct(0) == pytest.approx(50.0)


def test_symmetry():
    for cp in (10, 50, 100, 300, 800, 2000):
        assert winpct(cp) + winpct(-cp) == pytest.approx(100.0)


def test_monotonic_increasing():
    prev = -1.0
    for cp in range(-1500, 1501, 25):
        v = winpct(cp)
        assert v > prev
        prev = v


def test_matches_formula_independently():
    # Ricalcolo indipendente della formula dichiarata.
    for cp in (-250, -100, 0, 75, 250, 600):
        expected = 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-0.00368208 * cp)) - 1.0)
        assert winpct(cp) == pytest.approx(expected)


def test_saturation_bounds():
    # La formula satura entro [0, 100] (a estremi molto grandi il float arrotonda a 100/0).
    assert winpct(100000) <= 100.0
    assert winpct(-100000) >= 0.0
    assert winpct(5000) > 99.0


def test_mate_handling():
    assert winpct_from_score(None, mate=3) == 100.0     # chi muove matta
    assert winpct_from_score(None, mate=-3) == 0.0      # chi muove viene mattato
    assert winpct_from_score(120, None) == pytest.approx(winpct(120))
    assert winpct_from_score(None, None) == 50.0


def test_winpct_loss():
    assert winpct_loss(80.0, 55.0) == pytest.approx(25.0)
    assert winpct_loss(50.0, 52.0) == pytest.approx(-2.0)  # miglioramento


def test_accuracy_formula():
    # accuracy a perdita 0 ~ 3.1669*... => ~100 (circa)
    a0 = accuracy_from_loss(0.0)
    assert 95.0 < a0 <= 100.0
    # decresce con la perdita
    assert accuracy_from_loss(5.0) > accuracy_from_loss(20.0)
    assert accuracy_from_loss(-1.0) == pytest.approx(accuracy_from_loss(0.0))  # clamp a 0
