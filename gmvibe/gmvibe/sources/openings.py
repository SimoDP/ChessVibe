"""Nome dell'apertura dal dataset ``lichess-org/chess-openings`` (documentazione.txt §5.A).

Il nome dell'apertura NON viene mai da Claude: arriva dal dataset TSV. Il download
avviene a runtime nella cache locale (``gmvibe/data/openings/``, git-ignored); se la rete
non e' disponibile si usa un piccolo subset incluso nel pacchetto (``openings_fallback.tsv``)
cosi' i test girano offline.
"""

from __future__ import annotations

from pathlib import Path

import chess
import chess.pgn

from .. import paths

# File per lettera iniziale della mossa (dataset lichess-org/chess-openings).
_LETTERS = ("a", "b", "c", "d", "e")
_RAW_URL = "https://raw.githubusercontent.com/lichess-org/chess-openings/master/{letter}.tsv"


class OpeningBook:
    """Indice delle aperture: chiave = sequenza UCI di mosse, valore = (eco, nome)."""

    def __init__(self, entries: dict[str, tuple[str, str]]) -> None:
        self._entries = entries

    def __len__(self) -> int:
        return len(self._entries)

    def match(self, uci_moves: list[str]) -> tuple[str | None, int]:
        """Nome della linea piu' lunga che e' prefisso della sequenza, e sua lunghezza."""
        return self.match_full(uci_moves)[1:]

    def match_full(self, uci_moves: list[str]) -> tuple[str | None, str | None, int]:
        """``(eco, nome, lunghezza)`` della linea piu' lunga prefisso della sequenza."""
        best_eco: str | None = None
        best_name: str | None = None
        best_len = 0
        for i in range(1, len(uci_moves) + 1):
            entry = self._entries.get(" ".join(uci_moves[:i]))
            if entry is not None:
                best_eco, best_name = entry[0], entry[1]
                best_len = i
        return best_eco, best_name, best_len

    def name_for_uci(self, uci_moves: list[str]) -> str | None:
        """Nome dell'apertura (linea piu' lunga che e' prefisso della sequenza)."""
        return self.match(uci_moves)[0]

    def eco_for_uci(self, uci_moves: list[str]) -> str | None:
        return self.match_full(uci_moves)[0]

    def in_book(self, uci_moves: list[str]) -> bool:
        """Vero se la sequenza ESATTA e' una linea nominata (la partita e' ancora in teoria).

        Un semplice prefisso non basta: serve che l'ultima mossa rientri in una linea
        nota, altrimenti la partita ha gia' lasciato la teoria.
        """
        return " ".join(uci_moves) in self._entries

    def name_for_game(self, game: chess.pgn.Game) -> str | None:
        board = game.board()
        seq: list[str] = []
        for move in game.mainline_moves():
            seq.append(move.uci())
        return self.name_for_uci(seq)


def _parse_tsv(text: str) -> dict[str, tuple[str, str]]:
    """Converte il TSV (eco, name, pgn) in indice UCI -> (eco, nome)."""
    entries: dict[str, tuple[str, str]] = {}
    lines = text.splitlines()
    if not lines:
        return entries
    # header: eco\tname\tpgn
    for raw in lines[1:]:
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) < 3:
            continue
        eco, name, pgn = parts[0], parts[1], parts[2]
        seq = _pgn_moves_to_uci(pgn)
        if seq:
            entries[" ".join(seq)] = (eco, name)
    return entries


def _pgn_moves_to_uci(movetext: str) -> list[str]:
    game = chess.pgn.read_game(__import__("io").StringIO(movetext + " *"))
    if game is None:
        return []
    return [m.uci() for m in game.mainline_moves()]


def _download_tsvs(target_dir: Path) -> str | None:
    """Scarica i TSV nella cache locale. Ritorna il contenuto concatenato o None."""
    try:
        import urllib.request

        target_dir.mkdir(parents=True, exist_ok=True)
        chunks: list[str] = []
        for letter in _LETTERS:
            url = _RAW_URL.format(letter=letter)
            dest = target_dir / f"{letter}.tsv"
            if dest.exists():
                chunks.append(dest.read_text(encoding="utf-8"))
                continue
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = resp.read().decode("utf-8")
            dest.write_text(data, encoding="utf-8")
            chunks.append(data)
        return "\n".join(chunks)
    except Exception:
        return None


def load_opening_book(*, allow_download: bool = True) -> OpeningBook:
    """Carica l'OpeningBook: cache locale, poi download, infine fallback offline."""
    cache_dir = paths.openings_cache_dir()
    if cache_dir.exists():
        chunks = [p.read_text(encoding="utf-8") for p in sorted(cache_dir.glob("*.tsv"))]
        if chunks:
            entries: dict[str, tuple[str, str]] = {}
            for chunk in chunks:
                entries.update(_parse_tsv(chunk))
            if entries:
                return OpeningBook(entries)

    if allow_download:
        downloaded = _download_tsvs(cache_dir)
        if downloaded:
            return OpeningBook(_parse_tsv(downloaded))

    fallback = paths.fallback_openings_path()
    if fallback.exists():
        return OpeningBook(_parse_tsv(fallback.read_text(encoding="utf-8")))
    return OpeningBook({})
