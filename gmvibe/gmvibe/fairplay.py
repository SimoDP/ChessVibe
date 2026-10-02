"""Blocco fair play (documentazione.txt §8.3): nessuna analisi durante una partita live.

> Durante una partita live non deve esistere alcun accesso a motore, valutazioni o
> Claude. L'analisi si sblocca **solo** dopo la fine della partita.

Il rispetto della regola e' imposto **nel codice**, non nella UI: un unico stato
process-wide (:class:`FairPlayGate`) viene alzato quando una partita live inizia e
abbassato quando finisce. I due funnel di analisi — :func:`gmvibe.analysis.pipeline.
analyze_game` (motore) e :func:`gmvibe.ai_coach.coach.coach_analysis` (LLM) — chiamano
:func:`require_idle` come *prima* istruzione, quindi **qualsiasi** percorso (CLI, web,
batch, chiamata diretta) e' bloccato finche' lo stato e' attivo.

Usare un motore durante una partita rated viola le regole di Lichess e puo' portare al
ban dell'account: per questo la guardia e' centralizzata e non aggirabile dai chiamanti.

Modulo **senza dipendenze** (solo stdlib) per poter essere importato ovunque senza cicli.
"""

from __future__ import annotations

import threading

__all__ = [
    "FairPlayGate",
    "FairPlayViolation",
    "gate",
    "require_idle",
    "reset_gate",
]


class FairPlayViolation(RuntimeError):
    """Tentativo di analisi mentre una partita live e' in corso (§8.3)."""


class FairPlayGate:
    """Stato process-wide: ``game_in_progress == True`` blocca ogni analisi.

    Thread-safe: la sessione live alza/abbassa la guardia da un thread di streaming,
    mentre le richieste web/CLI la interrogano da altri thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._game_id: str | None = None

    # ------------------------------------------------------------------ #
    # Stato
    # ------------------------------------------------------------------ #
    def begin(self, game_id: str) -> None:
        """Segnala l'inizio di una partita live: da qui l'analisi e' vietata."""
        with self._lock:
            self._game_id = game_id

    def end(self, game_id: str | None = None) -> None:
        """Segnala la fine della partita live: l'analisi torna disponibile.

        Il rilascio avviene SOLO se ``game_id`` coincide con la partita attiva. Un
        ``game_id`` ``None`` (una sessione che non ha mai avuto una partita, o un id
        ignoto) NON sblocca nulla: altrimenti la fine di una sessione estranea
        azzererebbe la guardia di una partita in corso (§8.3). Per azzerare la guardia
        a prescindere (solo nei test) usare :meth:`reset`.
        """
        if game_id is None:
            return
        with self._lock:
            if self._game_id == game_id:
                self._game_id = None

    def reset(self) -> None:
        """Azzera la guardia a prescindere dalla partita attiva (solo per i test)."""
        with self._lock:
            self._game_id = None

    @property
    def active(self) -> bool:
        """True se una partita live e' in corso (``game_in_progress``)."""
        with self._lock:
            return self._game_id is not None

    @property
    def game_id(self) -> str | None:
        with self._lock:
            return self._game_id

    # ------------------------------------------------------------------ #
    # Guardia
    # ------------------------------------------------------------------ #
    def require_idle(self, *, action: str = "analisi") -> None:
        """Solleva :class:`FairPlayViolation` se una partita live e' in corso."""
        gid = self.game_id
        if gid is not None:
            raise FairPlayViolation(
                f"{action} vietata durante la partita live {gid} (§8.3). "
                "Nessun accesso a motore/LLM finche' la partita non e' finita."
            )

    def as_dict(self) -> dict[str, object]:
        """Stato serializzabile per l'API (``/api/live/state``, UI)."""
        with self._lock:
            return {"game_in_progress": self._game_id is not None, "game_id": self._game_id}


# Stato globale del processo: un solo gate, condiviso da CLI e server web.
_GATE = FairPlayGate()


def gate() -> FairPlayGate:
    """Ritorna il gate globale del processo."""
    return _GATE


def require_idle(*, action: str = "analisi") -> None:
    """Scorciatoia: :meth:`FairPlayGate.require_idle` sul gate globale."""
    _GATE.require_idle(action=action)


def reset_gate() -> None:
    """Azzera il gate (solo per i test: isola uno scenario dall'altro)."""
    _GATE.reset()
