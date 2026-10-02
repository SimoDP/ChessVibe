"""Risoluzione dei path del progetto.

Nessun path assoluto e' scritto nel codice (documentazione.txt §11): la radice viene
dedotta dalla posizione dei file di configurazione, con override via variabili
d'ambiente per i test.
"""

from __future__ import annotations

import os
from pathlib import Path

# Variabili d'ambiente per override (usate dai test e per installazioni non standard).
ENV_ROOT = "GMVIBE_ROOT"
ENV_DB = "GMVIBE_DB"
ENV_STOCKFISH = "GMVIBE_STOCKFISH"


def project_root() -> Path:
    """Directory che contiene ``levels.yaml`` / ``rating_conversion.yaml``.

    Ordine: variabile d'ambiente ``GMVIBE_ROOT``, altrimenti la directory padre del
    pacchetto ``gmvibe`` (cioe' la radice del repo).
    """
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def levels_path() -> Path:
    return project_root() / "levels.yaml"


def rating_conversion_path() -> Path:
    return project_root() / "rating_conversion.yaml"


def profiles_dir() -> Path:
    """``profiles/`` — una sottocartella per utente (git-ignored)."""
    return project_root() / "profiles"


def profile_dir(nickname: str) -> Path:
    return profiles_dir() / nickname


def profile_path(nickname: str) -> Path:
    return profile_dir(nickname) / "profile.yaml"


def secrets_path(nickname: str) -> Path:
    """File dei segreti per profilo (chmod 600, git-ignored)."""
    return profile_dir(nickname) / "secrets.env"


def default_db_path() -> Path:
    """Percorso del DB SQLite (cache motore condivisa + dati utente)."""
    env = os.environ.get(ENV_DB)
    if env:
        return Path(env).expanduser()
    return project_root() / "gmvibe.db"


def openings_cache_dir() -> Path:
    """Cache locale del dataset chess-openings (git-ignored)."""
    return Path(__file__).resolve().parent / "data" / "openings"


def fallback_openings_path() -> Path:
    """Piccolo subset di apertura incluso nel pacchetto, per uso offline/test."""
    return Path(__file__).resolve().parent / "data" / "openings_fallback.tsv"


def web_static_dir() -> Path:
    """Cartella degli asset statici della UI (HTML/JS/CSS + vendor, FASE 6)."""
    return Path(__file__).resolve().parent / "web" / "static"
