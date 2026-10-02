"""Package prompt — file .md caricati come system e prompt di analisi."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Carica un prompt ``<name>.md`` dalla cartella prompts/.

    Raises:
        FileNotFoundError: se il file non esiste.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt non trovato: {path}")
    return path.read_text(encoding="utf-8")


__all__ = ["load_prompt"]
