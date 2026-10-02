"""Cache SQLite per risposte LLM.

La chiave e' un hash del contenuto della richiesta (prompt + modello + parametri).
Usata per evitare chiamate ripetute allo stesso messaggio durante una sessione.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .. import paths

_DB_NAME = "gmvibe_ai_coach_cache.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS response_cache (
    key         TEXT PRIMARY KEY,
    prompt_hash TEXT NOT NULL,
    model       TEXT NOT NULL,
    response    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def _prompt_hash(
    *,
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    system: str = "",
) -> str:
    """Hash deterministico della richiesta.

    Il ``system`` FA parte della chiave: due richieste con lo stesso messaggio ma system
    diverso (es. lingua/livello diversi) producono risposte diverse e non devono
    collidere in cache.
    """
    blob = json.dumps(
        {"system": system, "messages": messages, "model": model,
         "max_tokens": max_tokens, "temperature": temperature},
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class AICache:
    """Cache per risposte LLM. Thread-safe in lettura, singola-writer via SQLite."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or paths.project_root() / _DB_NAME
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        try:
            yield conn
        finally:
            conn.close()

    def get(self, *, messages: list[dict[str, str]], model: str, max_tokens: int,
            temperature: float, system: str = "") -> str | None:
        """Ritorna la risposta cacheata o None."""
        h = _prompt_hash(messages=messages, model=model, max_tokens=max_tokens,
                         temperature=temperature, system=system)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT response FROM response_cache WHERE prompt_hash = ?", (h,)
            ).fetchone()
        return row[0] if row else None

    def put(
        self,
        response: str,
        *,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
        system: str = "",
    ) -> None:
        """Memorizza la risposta nella cache."""
        h = _prompt_hash(messages=messages, model=model, max_tokens=max_tokens,
                         temperature=temperature, system=system)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO response_cache (key, prompt_hash, model, response) VALUES (?, ?, ?, ?)",
                (h, h, model, response),
            )
            conn.commit()

    def clear(self) -> int:
        """Pulisce la cache. Ritorna il numero di righe eliminate."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM response_cache")
            conn.commit()
            return cur.rowcount
