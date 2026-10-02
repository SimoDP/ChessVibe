"""Test di caricamento config e dei segreti (documentazione.txt §4)."""

from __future__ import annotations

import os
import stat

import pytest

from gmvibe import config as cfg
from gmvibe.models import PlayerProfile, Secrets


def test_load_levels(levels):
    assert len(levels.anchors) == 4
    assert [a.rating for a in levels.sorted_anchors()] == [1400, 1700, 2000, 2300]
    assert levels.fast_pass.depth == 14


def test_load_conversion(conversion):
    assert "fide" in conversion.tables
    assert conversion.clamp.min == 400


def test_save_and_load_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("GMVIBE_ROOT", str(tmp_path))
    p = PlayerProfile(nickname="simo", rating=1900, rating_tipo="fide")
    path = cfg.save_profile(p)
    assert path.exists()
    loaded = cfg.load_profile("simo")
    assert loaded.nickname == "simo"
    assert loaded.rating == 1900
    # il profilo non contiene segreti
    text = path.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in text
    assert "TOKEN" not in text


def test_list_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("GMVIBE_ROOT", str(tmp_path))
    assert cfg.list_profiles() == []
    cfg.save_profile(PlayerProfile(nickname="a", rating=1500))
    cfg.save_profile(PlayerProfile(nickname="b", rating=2000))
    assert cfg.list_profiles() == ["a", "b"]


def test_secrets_file_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("GMVIBE_ROOT", str(tmp_path))
    path = cfg.save_secrets("simo", Secrets(), use_keyring=False)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_mask_secret():
    assert cfg.mask_secret(None) == "<unset>"
    assert cfg.mask_secret("") == "<unset>"
    assert cfg.mask_secret("supersecrettoken") == "****oken"
    # il valore completo NON compare mai
    assert "supersecret" not in cfg.mask_secret("supersecrettoken")


def test_no_hardcoded_personal_values():
    """La config non deve contenere Elo o username scritti a mano."""
    import gmvibe.config as mod

    source = open(mod.__file__, encoding="utf-8").read()
    for forbidden in ("1900", "2200", "simo", "/home/", "C:\\"):
        assert forbidden not in source
