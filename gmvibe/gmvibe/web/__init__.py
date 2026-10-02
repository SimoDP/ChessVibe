"""Server FastAPI + UI di review (documentazione.txt §9, §10 FASE 6).

Il lavoro pesante (Stockfish, layer LLM) gira in **thread** dietro una coda
in-process (§3: niente Celery); la pagina fa polling sullo stato del job. Il codice
deterministico calcola, la UI **mostra** — nessun ricalcolo scacchistico nel browser
oltre alla legalità delle varianti (chess.js).

Factory uvicorn: ``gmvibe.web.app:create_app``.
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
