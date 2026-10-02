"""Coda in-process dei job lenti (documentazione.txt §3, §10 FASE 6).

Un job è una funzione eseguita in un thread separato (analisi Stockfish, chiamata
LLM). Il browser riceve subito un ``job_id`` e fa polling su :meth:`JobRegistry.get`.
I job **gravi** (motore + LLM) sono serializzati da un lock: sono locali e pesanti,
eseguirne due in parallelo saturerebbe la CPU senza beneficio.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["Job", "JobRegistry"]

# Stati possibili di un job.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"


@dataclass
class Job:
    """Stato di un job asincrono."""

    id: str
    kind: str
    status: str = QUEUED
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        start = self.started_at if self.started_at is not None else self.created_at
        return round(end - start, 2)

    def as_dict(self) -> dict[str, Any]:
        """Serializzazione per l'API (mai eccezioni/stacktrace verso il browser)."""
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "elapsed": self.elapsed,
            "error": self.error,
            **self.result,
        }


class JobRegistry:
    """Registro in-memory dei job; esecuzione in thread, stato sotto lock."""

    def __init__(self, *, max_jobs: int = 200) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Un solo job pesante alla volta (motore/LLM): CPU locale.
        self._run_lock = threading.Lock()
        self._max_jobs = max_jobs

    def submit(self, kind: str, fn: Callable[[], dict[str, Any] | None]) -> Job:
        """Accoda ``fn`` ed esegue in un thread. Ritorna il :class:`Job` appena creato."""
        job = Job(id=uuid.uuid4().hex, kind=kind)
        with self._lock:
            self._jobs[job.id] = job
            self._prune()

        def _run() -> None:
            with self._run_lock:
                with self._lock:
                    job.status = RUNNING
                    job.started_at = time.monotonic()
                try:
                    result = fn() or {}
                    with self._lock:
                        job.result = dict(result)
                        job.status = DONE
                except Exception as exc:  # noqa: BLE001 - l'errore va mostrato all'utente
                    with self._lock:
                        job.error = f"{type(exc).__name__}: {exc}"
                        job.status = ERROR
                    traceback.print_exc()
                finally:
                    with self._lock:
                        job.finished_at = time.monotonic()

        threading.Thread(target=_run, name=f"gmvibe-job-{job.id[:8]}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _prune(self) -> None:
        """Tiene solo gli ultimi ``max_jobs`` job (i più vecchi terminati spariscono)."""
        if len(self._jobs) <= self._max_jobs:
            return
        ordered = sorted(self._jobs.values(), key=lambda j: j.created_at)
        for old in ordered[: len(self._jobs) - self._max_jobs]:
            if old.status in (DONE, ERROR):
                self._jobs.pop(old.id, None)
