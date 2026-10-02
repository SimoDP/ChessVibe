"""Generatore di puzzle e ripasso dilazionato (documentazione.txt §7.1).

Ogni errore significativo diventa un **puzzle**: la FEN prima della mossa sbagliata, la
soluzione **calcolata dal motore** (mai dall'LLM: `best_move_uci`/`deep_lines` della
riga), un tema (l'etichetta §5.G) e una spiegazione **deterministica** costruita dalla
natura dell'errore e dai fatti posizionali.

Il ripasso è un Leitner semplice: ``next_box``/``box_due`` sono funzioni pure sugli
intervalli di ``levels.yaml`` (tutte le soglie vivono in config, mai nel codice).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..models import GameAnalysis, MoveRow, Puzzle, PuzzleConfig

# Spiegazione deterministica per natura d'errore (§5.G). Nessun testo generato dall'LLM.
_THEME_TEXT = {
    "tattica_mancata": "c'era una tattica da trovare (forzante) e non è stata vista",
    "pezzo_in_presa": "un pezzo è stato lasciato in presa",
    "valutazione_errata": "la valutazione della posizione è stata sbagliata",
    "piano_sbagliato": "il piano scelto era meno efficace della mossa del motore",
    "profilassi_mancata": "mancava una mossa di profilassi contro l'idea dell'avversario",
    "sicurezza_re": "lo scudo del Re si è indebolito",
    "tecnica_conversione": "posizione vinta non convertita con la tecnica giusta",
    "tecnica_difesa": "la difesa poteva essere più tenace",
    "finale_tecnica": "finale giocato tecnicamente in modo impreciso",
    "tempo": "errore favorito dalla gestione del tempo",
    "teoria": "uscita dalla teoria con una mossa inferiore",
}


def _theme_explanation(row: MoveRow) -> str:
    """Spiegazione deterministica: natura dell'errore + fatti posizionali + soluzione."""
    parts: list[str] = []
    base = _THEME_TEXT.get(row.error_label or "")
    if base:
        parts.append(base.capitalize() + ".")
    if row.best_move_san:
        parts.append(f"La mossa migliore era {row.best_move_san}.")
    if row.winpct_loss is not None:
        parts.append(f"Perdita stimata: {row.winpct_loss:.1f} punti di win%.")

    fb = row.features_before
    if fb is not None:
        side = fb.white if row.color == "white" else fb.black
        facts: list[str] = []
        if side.castled and side.king_pawn_shield < 2:
            facts.append("scudo del Re ridotto")
        if side.isolated_pawns:
            facts.append("pedoni isolati: " + ", ".join(side.isolated_pawns))
        if side.hanging_pawns:
            facts.append("pedoni sospesi")
        if not facts and fb.open_files:
            facts.append("colonne aperte: " + ", ".join(fb.open_files))
        if facts:
            parts.append("Fatti posizionali: " + "; ".join(facts) + ".")
    return " ".join(parts).strip()


def _alternatives(row: MoveRow, solution_uci: str) -> list[str]:
    """Altre mosse del motore accettabili come soluzione (dai deep_lines), senza duplicati."""
    out: list[str] = []
    for line in row.deep_lines:
        uci = line.move_uci
        if uci and uci != solution_uci and uci not in out:
            out.append(uci)
    return out[:3]


def generate_puzzles(
    analysis: GameAnalysis,
    *,
    user_id: str,
    game_id: int | None = None,
    config: PuzzleConfig | None = None,
) -> list[Puzzle]:
    """Genera i puzzle dagli errori significativi della partita (§7.1).

    Un errore diventa puzzle se è ``errore``/``svarione``, se la perdita di win% è almeno
    ``min_winpct_loss`` e se il motore indica una mossa migliore. Si prendono i
    ``max_per_game`` peggiori. La soluzione è sempre la mossa del motore.
    """
    cfg = config or PuzzleConfig()
    if not cfg.enabled:
        return []

    candidates = [
        row for row in analysis.rows
        if row.label in ("errore", "svarione")
        and row.best_move_uci
        and (row.winpct_loss or 0.0) >= cfg.min_winpct_loss
    ]
    candidates.sort(key=lambda r: (-(r.winpct_loss or 0.0), r.ply))

    puzzles: list[Puzzle] = []
    for row in candidates[: cfg.max_per_game]:
        puzzles.append(
            Puzzle(
                user_id=user_id,
                game_id=game_id,
                ply=row.ply,
                fen=row.fen_before,
                solution_uci=row.best_move_uci,
                solution_san=row.best_move_san,
                alternatives=_alternatives(row, row.best_move_uci),
                theme=row.error_label,
                explanation=_theme_explanation(row),
            )
        )
    return puzzles


# --------------------------------------------------------------------------- #
# Ripasso dilazionato (Leitner) — funzioni pure sugli intervalli di config
# --------------------------------------------------------------------------- #
def _clamp_box(box: int, intervals: list[int]) -> int:
    if not intervals:
        return 1
    return max(1, min(int(box), len(intervals)))


def next_box(box: int, correct: bool, intervals: list[int]) -> int:
    """Box successivo (§7.1): corretto → box+1 (fino all'ultimo); sbagliato → box 1."""
    box = _clamp_box(box, intervals)
    if not correct:
        return 1
    return _clamp_box(box + 1, intervals)


def box_due(box: int, intervals: list[int], now: datetime | None = None) -> datetime:
    """Data di prossimo ripasso per un box: ``now + intervals[box-1]`` giorni."""
    now = now or datetime.now(timezone.utc)
    if not intervals:
        return now
    days = intervals[_clamp_box(box, intervals) - 1]
    return now + timedelta(days=int(days))


def is_due(due_at: str | None, now: datetime | None = None) -> bool:
    """Vero se il puzzle è da ripassare (mai ripassato o scaduto)."""
    if not due_at:
        return True
    now = now or datetime.now(timezone.utc)
    try:
        when = datetime.fromisoformat(due_at)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when <= now


def solve(solved_uci: str, puzzle) -> bool:
    """Correttezza di una soluzione: la mossa del motore o una delle alternative.

    Accetta sia un :class:`~gmvibe.models.Puzzle` sia una riga del DB (dict).
    """
    if not solved_uci:
        return False
    if isinstance(puzzle, dict):
        solution = puzzle.get("solution_uci")
        alternatives = json.loads(puzzle.get("alternatives_json") or "[]")
    else:
        solution = puzzle.solution_uci
        alternatives = puzzle.alternatives or []
    return solved_uci in {solution, *alternatives}
