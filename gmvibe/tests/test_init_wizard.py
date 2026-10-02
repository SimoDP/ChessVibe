"""Test del wizard ``init`` (documentazione.txt §4.5)."""

from __future__ import annotations

import argparse
import os
import stat

import pytest

from gmvibe import config as cfg
from gmvibe.cli import init_wizard


@pytest.fixture
def scripted_input(monkeypatch):
    """Sostituisce input() con una lista predefinita di risposte."""

    def make(answers):
        it = iter(answers)

        def fake_input(prompt=""):
            try:
                return next(it)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", fake_input)

    return make


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    """Evita di toccare il keyring di sistema durante i test."""
    import keyring

    monkeypatch.setattr(keyring, "set_password", lambda *a, **k: None)
    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: None)


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """GMVIBE_ROOT temporaneo con copiati i file di config del repo."""
    from pathlib import Path
    import shutil

    repo = Path(__file__).resolve().parent.parent
    for name in ("levels.yaml", "rating_conversion.yaml"):
        shutil.copy(repo / name, tmp_path / name)
    monkeypatch.setenv("GMVIBE_ROOT", str(tmp_path))
    return tmp_path


def test_wizard_creates_valid_profile(tmp_root, scripted_input, capsys):
    secret = "sk-supersecret-abcdef"
    # nickname, lingua, rating, tipo, cadenza, conferma, user_lich, user_cc,
    # anthropic, token, conferma_demo
    scripted_input(["simo", "it", "1900", "fide", "blitz", "", "", "", secret, "", "n"])

    rc = init_wizard.run(argparse.Namespace())
    assert rc == 0

    profile = cfg.load_profile("simo")
    assert profile.nickname == "simo"
    assert profile.rating == 1900
    assert profile.rating_tipo == "fide"

    # il segreto NON deve comparire nell'output
    out = capsys.readouterr().out
    assert secret not in out


def test_wizard_secrets_permissions(tmp_root, scripted_input):
    scripted_input(["simo", "it", "1900", "fide", "blitz", "", "", "", "sk-abc12345", "", "n"])
    init_wizard.run(argparse.Namespace())

    secrets_path = tmp_root / "profiles" / "simo" / "secrets.env"
    assert secrets_path.exists()
    mode = stat.S_IMODE(os.stat(secrets_path).st_mode)
    assert mode == 0o600


def test_wizard_two_profiles_differ(tmp_root, scripted_input, levels, conversion):
    """1900 e 2200 producono LevelConfig diversi (criterio §10)."""
    from gmvibe.level import resolve_level

    scripted_input(["a", "it", "1900", "fide", "blitz", "", "", "", "", "", "n"])
    init_wizard.run(argparse.Namespace())
    scripted_input(["b", "it", "2200", "fide", "blitz", "", "", "", "", "", "n"])
    init_wizard.run(argparse.Namespace())

    la = resolve_level(cfg.load_profile("a"), levels, conversion)
    lb = resolve_level(cfg.load_profile("b"), levels, conversion)
    assert la.error_thresholds.errore != lb.error_thresholds.errore


def test_wizard_no_nickname(tmp_root, scripted_input):
    scripted_input([""])
    assert init_wizard.run(argparse.Namespace()) == 2
