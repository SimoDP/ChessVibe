"""Comando ``serve``: avvia il server web con la UI di review (§9, FASE 6).

Il pacchetto web è opzionale (extra ``.[web]``): se manca, l'errore è esplicito.
Si usa la *factory* ``gmvibe.web.app:create_app`` così ``--reload`` può reimportare
l'app senza ricreare la connessione al DB nel processo padre in modo implicito.
"""

from __future__ import annotations

import argparse
import sys

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "il server web richiede FastAPI/uvicorn: pip install -e \".[web]\"",
            file=sys.stderr,
        )
        return 1

    print(f"GM-Vibe — UI di review su http://{args.host}:{args.port}  (Ctrl-C per uscire)")
    uvicorn.run(
        "gmvibe.web.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0
