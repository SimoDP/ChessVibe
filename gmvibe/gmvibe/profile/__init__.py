"""Analisi cross-partita (documentazione.txt §7): profilo di debolezze e puzzle.

- :mod:`gmvibe.profile.weaknesses` — profilo aggregato sulle ultime N partite (§7);
- :mod:`gmvibe.profile.puzzles`    — generatore di esercizi + ripasso Leitner (§7.1).

Il motore calcola, il codice misura: qui si aggregano dati già prodotti dalla pipeline
(nessun ricalcolo scacchistico, nessuna generazione di mosse da parte dell'LLM).
"""

from __future__ import annotations

from .puzzles import (
    box_due,
    generate_puzzles,
    is_due,
    next_box,
    solve,
)
from .weaknesses import build_weakness_profile, recurrence_by_label

__all__ = [
    "build_weakness_profile",
    "recurrence_by_label",
    "generate_puzzles",
    "next_box",
    "box_due",
    "is_due",
    "solve",
]
