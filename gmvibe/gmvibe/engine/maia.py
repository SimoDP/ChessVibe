"""Maia via Lc0: probabilita' umana di una mossa (documentazione.txt §5.H, FASE 8A).

Maia e' una rete che gioca **come un umano di un certo livello**; la sua *policy* dice con
quanta probabilita' un giocatore di quel livello giocherebbe ogni mossa. Serve al **fattore
5** del Complexity Score: se la mossa migliore del motore e' poco probabile per gli umani,
la posizione e' davvero difficile.

E' **opzionale** (§4.2): se Lc0 o i pesi Maia non sono installati, il fattore 5 si disattiva
e il Complexity Score resta quello dei fattori 1-4. Il modello/percorso dei pesi arriva da
``levels.yaml`` via ``resolve_level`` (CLAUDE.md): nessun valore hardcoded.

Fatti API / limiti (onesti):
- Lc0 parla UCI ma la *policy* per mossa non passa da UCI: si legge dall'output
  ``--verbose-move-stats`` (stderr). Il formato varia tra versioni: :func:`parse_move_stats`
  e' tollerante e va validato sull'installazione reale (test ``slow``).
- I pesi Maia (``maia-1100.pb.gz`` …) non sono inclusi nel repo: vanno scaricati a parte.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

import chess

__all__ = [
    "Lc0MaiaClient",
    "MaiaClient",
    "MaiaError",
    "build_maia_client",
    "find_lc0",
    "maia_availability",
    "parse_move_stats",
]

_COMMON_LC0_PATHS = ("/usr/games/lc0", "/usr/local/bin/lc0", "/usr/bin/lc0")

# Riga stile Lc0 "--verbose-move-stats": "  e2e4 (N: 12) (P: 21.34%) (Q: 0.02) ...".
# Tollera anche "e2e4 21.34%" o "info string e2e4 P: 21.34%".
_MOVE_STATS_RE = re.compile(
    r"(?P<move>[a-h][1-8][a-h][1-8][qrbn]?)\b[^\n%]*?"
    r"(?:\(?P:\s*)?(?P<pct>\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)


class MaiaError(Exception):
    """Errore nell'esecuzione di Lc0/Maia (binario mancante, pesi assenti, output illeggibile)."""


def find_lc0(path: str | None = None) -> str | None:
    """Trova il binario Lc0: ``path``/config, env ``GMVIBE_LC0``, PATH, percorsi comuni."""
    for candidate in (path, os.environ.get("GMVIBE_LC0")):
        if candidate and Path(candidate).exists():
            return candidate
    found = shutil.which("lc0")
    if found:
        return found
    for candidate in _COMMON_LC0_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def maia_availability(config) -> tuple[bool, bool]:
    """Rileva ``(maia2_disponibile, maia1_disponibile)`` per ``resolve_level`` (§4.3.2).

    Serve a decidere il KIND del modello prima di costruirlo. Heuristica prudente: Maia-2
    (rating diretto) se i pesi contengono "maia2"; Maia-1 (bucket) se c'e' un pattern
    ``maia-<bucket>``. Nessun Lc0/pesi → ``(False, False)`` e Maia resta disattivato.
    """
    if find_lc0(getattr(config, "lc0_path", None)) is None:
        return False, False
    weights_dir = getattr(config, "weights_dir", None)
    if not weights_dir:
        return False, False
    d = Path(weights_dir)
    if not d.is_dir():
        return False, False
    names = [p.name for p in d.glob("*.pb.gz")]
    has2 = any("maia2" in n.lower() or "maia-2" in n.lower() for n in names)
    has1 = bool(names) and not has2
    return has2, has1


def parse_move_stats(text: str) -> dict[str, float]:
    """Estrae ``{mossa_uci: probabilita' 0..1}`` dall'output ``--verbose-move-stats`` di Lc0.

    Tollerante al formato (vedi ``_MOVE_STATS_RE``). A parita' di mossa tiene il massimo.
    """
    out: dict[str, float] = {}
    for mo in _MOVE_STATS_RE.finditer(text or ""):
        move = mo.group("move").lower()
        try:
            pct = float(mo.group("pct"))
        except ValueError:  # pragma: no cover - il regex impone un numero
            continue
        prob = max(0.0, min(1.0, pct / 100.0))
        out[move] = max(out.get(move, 0.0), prob)
    return out


@runtime_checkable
class MaiaClient(Protocol):
    """Interfaccia duck-typed di un client Maia (implementata da Lc0 e dai finti nei test)."""

    def human_move_prob(self, board: chess.Board, move_uci: str) -> float | None:
        """Probabilita' (0..1) che un umano del livello giochi ``move_uci``; ``None`` se ignota."""
        ...

    def __enter__(self) -> "MaiaClient": ...

    def __exit__(self, *exc) -> None: ...


class Lc0MaiaClient:
    """Client Maia basato su ``lc0`` (subprocess UCI + parsing della policy).

    Usare come context manager. Se il binario manca solleva :class:`MaiaError`: la
    degradazione (fattore 5 disattivato) e' responsabilita' di :func:`build_maia_client`.
    """

    def __init__(
        self,
        *,
        lc0_path: str,
        weights: str,
        extra_args: list[str] | None = None,
        nodes: int = 1,
        time: float | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._lc0_path = lc0_path
        self._weights = weights
        self._extra_args = list(extra_args or [])
        self._nodes = int(nodes)
        self._time = time
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------ #
    # Ciclo di vita
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "Lc0MaiaClient":
        if not Path(self._lc0_path).exists():
            raise MaiaError(f"Lc0 non trovato in {self._lc0_path}")
        if not Path(self._weights).exists():
            raise MaiaError(f"pesi Maia non trovati: {self._weights}")
        cmd = [self._lc0_path, f"--weights={self._weights}", *self._extra_args]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
            )
        except OSError as exc:  # pragma: no cover - dipende dall'ambiente
            raise MaiaError(f"avvio di Lc0 fallito: {exc}") from exc
        self._send("uci")
        self._send("isready")
        return self

    def __exit__(self, *exc) -> None:
        if self._proc is not None:
            try:
                self._send("quit")
            except Exception:  # pragma: no cover - best-effort
                pass
            finally:
                self._proc.kill()
                self._proc = None

    def _send(self, line: str) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    # ------------------------------------------------------------------ #
    # Policy
    # ------------------------------------------------------------------ #
    def human_move_prob(self, board: chess.Board, move_uci: str) -> float | None:
        """Probabilita' umana della mossa: una ricerca a pochi nodi stampa la policy."""
        assert self._proc is not None
        go = f"go nodes {self._nodes}" if self._time is None else f"go time {int(self._time * 1000)}"
        self._send(f"position fen {board.fen()}")
        self._send(go)
        stats = self._drain_until_bestmove()
        probs = parse_move_stats(stats)
        return probs.get(move_uci.lower())

    def _drain_until_bestmove(self) -> str:
        """Legge stderr (verbose-move-stats) fino a ``bestmove``, con timeout."""
        import selectors

        assert self._proc is not None and self._proc.stderr is not None
        chunks: list[str] = []
        sel = selectors.DefaultSelector()
        sel.register(self._proc.stderr, selectors.EVENT_READ)
        try:
            while True:
                if not sel.select(self._timeout):
                    break
                line = self._proc.stderr.readline()
                if not line:
                    break
                chunks.append(line)
                if line.startswith("bestmove"):
                    break
        finally:
            sel.close()
        return "".join(chunks)


def build_maia_client(
    *,
    enabled: bool,
    maia_kind: str,
    maia_model: str | None,
    config,
) -> Lc0MaiaClient | None:
    """Costruisce il client Maia o ritorna ``None`` (degradazione pulita, §4.2).

    ``maia_kind == "none"`` (Lc0 assente o Maia disattivato in config) → nessun client e
    il Complexity Score usa i soli fattori 1-4.
    """
    if not enabled or maia_kind == "none" or not maia_model:
        return None
    lc0 = find_lc0(getattr(config, "lc0_path", None))
    if lc0 is None:
        return None
    weights_dir = getattr(config, "weights_dir", None)
    if not weights_dir:
        return None
    # Maia-2 usa il rating; Maia-1 un bucket. Il nome file arriva dal pattern in config.
    bucket = maia_model.split("-")[-1] if maia_kind == "maia1" else maia_model
    weights = Path(weights_dir) / getattr(config, "weights_pattern", "maia-{bucket}.pb.gz").format(
        bucket=bucket, model=maia_model
    )
    if not weights.exists():
        return None
    return Lc0MaiaClient(
        lc0_path=lc0,
        weights=str(weights),
        extra_args=list(getattr(config, "lc0_args", []) or []),
        nodes=getattr(config, "nodes", 1),
        time=getattr(config, "time", None),
    )
