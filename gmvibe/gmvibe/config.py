"""Caricamento di profilo, livelli, conversione rating e segreti (documentazione.txt §4).

Regole:
- Nessun valore personale hardcoded: tutto viene da file o da variabili d'ambiente.
- ``profile.yaml`` non contiene mai segreti; i segreti stanno in ``secrets.env`` o nel
  keyring di sistema, con il keyring che ha la precedenza (§4.4).
- ``mask_secret`` va usata in ogni log/messaggio d'errore: un segreto non deve mai
  comparire in chiaro.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from . import paths
from .models import LevelsConfig, PlayerProfile, RatingConversion, Secrets

KEYRING_SERVICE = "gmvibe"
_SECRET_ENV_NAMES = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "lichess_token": "LICHESS_TOKEN",
}


class ConfigError(Exception):
    """Errore di configurazione (profilo mancante/invalido, YAML malformato, ...)."""


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"file di configurazione non trovato: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:  # pragma: no cover - messaggio dipendente da PyYAML
        raise ConfigError(f"YAML non valido in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"contenuto inatteso in {path}: atteso un mapping YAML")
    return data


def load_levels(path: Path | None = None) -> LevelsConfig:
    """Carica ``levels.yaml`` (punti di ancoraggio + fast_pass + maia)."""
    data = _load_yaml(path or paths.levels_path())
    try:
        return LevelsConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"levels.yaml non valido: {exc}") from exc


def load_rating_conversion(path: Path | None = None) -> RatingConversion:
    """Carica ``rating_conversion.yaml``."""
    data = _load_yaml(path or paths.rating_conversion_path())
    try:
        return RatingConversion.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"rating_conversion.yaml non valido: {exc}") from exc


def load_profile(nickname: str) -> PlayerProfile:
    """Carica ``profiles/<nickname>/profile.yaml`` (mai i segreti)."""
    path = paths.profile_path(nickname)
    data = _load_yaml(path)
    try:
        return PlayerProfile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"profile.yaml non valido per '{nickname}': {exc}") from exc


def save_profile(profile: PlayerProfile) -> Path:
    """Scrive ``profile.yaml`` per il profilo indicato. Ritorna il path scritto."""
    path = paths.profile_path(profile.nickname)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            profile.model_dump(exclude_none=True),
            fh,
            sort_keys=False,
            allow_unicode=True,
        )
    return path


def list_profiles() -> list[str]:
    """Nomi dei profili presenti in ``profiles/`` (vuoto se la cartella non esiste)."""
    d = paths.profiles_dir()
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "profile.yaml").exists())


def mask_secret(value: str | None) -> str:
    """Maschera un segreto per il logging: ``****abcd`` (mai il valore in chiaro)."""
    if not value:
        return "<unset>"
    tail = value[-4:] if len(value) > 4 else ""
    return f"****{tail}"


# --------------------------------------------------------------------------- #
# Segreti (§4.4): keyring con precedenza, .env come fallback garantito
# --------------------------------------------------------------------------- #
def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - dotenv e' una dipendenza dichiarata
        return {}
    values = dotenv_values(path)
    return {k: v for k, v in values.items() if v is not None}


def _keyring_get(name: str) -> str | None:
    """Legge un segreto dal keyring di sistema; best-effort su Linux headless."""
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        # keyring senza backend (es. SecretService assente) o errore di sistema:
        # si degrada silenziosamente al file .env, come da §4.4.
        return None


def load_secrets(nickname: str) -> Secrets:
    """Carica i segreti per profilo: keyring prima, ``.env`` come fallback (§4.4)."""
    env_values = _read_env_file(paths.secrets_path(nickname))
    resolved: dict[str, str | None] = {}
    for field, env_name in _SECRET_ENV_NAMES.items():
        value = _keyring_get(f"{nickname}:{env_name}") or env_values.get(env_name)
        resolved[field] = value or None
    return Secrets(**resolved)


def save_secrets(nickname: str, secrets: Secrets, *, use_keyring: bool = True) -> Path:
    """Scrive i segreti in ``secrets.env`` con permessi ristretti (chmod 600).

    Ritorna il path scritto. Il file e' git-ignored (vedi .gitignore).
    """
    path = paths.secrets_path(nickname)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Scritto da `gmvibe init`. Non committare. chmod 600.",
        "",
    ]
    for field, env_name in _SECRET_ENV_NAMES.items():
        secret = getattr(secrets, field)
        value = secret.get_secret_value() if secret is not None else ""
        lines.append(f"{env_name}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    if use_keyring:
        for field, env_name in _SECRET_ENV_NAMES.items():
            secret = getattr(secrets, field)
            if secret is None:
                continue
            try:
                import keyring

                keyring.set_password(KEYRING_SERVICE, f"{nickname}:{env_name}", secret.get_secret_value())
            except Exception:
                pass  # best-effort
    return path
