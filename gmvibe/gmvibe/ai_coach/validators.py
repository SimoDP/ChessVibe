"""Validazione obbligatoria dell'output LLM (documentazione.txt §6.4).

Regole non negoziabili (CLAUDE.md):
- Nessuna variante generata dall'LLM viene mostrata senza validazione di legalità.
- Il motore calcola, Claude spiega: ogni mossa citata è verificata con python-chess.

Cosa fa la validazione (§6.4):
1. ogni variante in ``varianti_citate`` deve essere **legale a partire dal suo
   ``fen_partenza``** (non dallo startpos!);
2. i FEN e gli id dei momenti citati devono esistere tra i dati forniti al motore;
3. le estensioni oltre le linee del motore vengono segnalate per la verifica a
   Stockfish (nel coach, non qui): ``kind="extension"`` non è bloccante.

In caso di fallimento il coach rigenera **una volta** passando l'errore esatto a
Claude; se fallisce ancora, si mostra il momento con i soli dati del motore (§6.4).
"""

from __future__ import annotations

import chess
from pydantic import BaseModel, Field

from ..models import CoachReport, CriticalMoment

__all__ = [
    "ValidationIssue",
    "ValidationResult",
    "check_variation_legality",
    "validate_report",
    "sanitize_llm_response",
]


class ValidationIssue(BaseModel):
    """Un problema rilevato nell'output LLM."""

    kind: str            # illegal_move | unknown_fen | empty_variation | unknown_moment | extension
    message: str
    moment_id: str | None = None
    variation_index: int | None = None
    blocking: bool = True


class ValidationResult(BaseModel):
    """Esito della validazione di un ``CoachReport``."""

    issues: list[ValidationIssue] = Field(default_factory=list)
    total_variations: int = 0
    legal_variations: int = 0
    grounding_failures: int = 0

    @property
    def ok(self) -> bool:
        """Vero se nessun problema bloccante (le estensioni non bloccano)."""
        return not any(i.blocking for i in self.issues)

    @property
    def legal_ratio(self) -> float:
        """% di varianti legali (obiettivo non negoziabile: 100%)."""
        if self.total_variations == 0:
            return 1.0
        return self.legal_variations / self.total_variations

    def error_text(self) -> str:
        """Testo dell'errore da rimandare a Claude per la rigenerazione."""
        lines = [
            f"- [{i.kind}] {i.message}"
            + (f" (momento {i.moment_id})" if i.moment_id else "")
            + (f" (variante #{i.variation_index})" if i.variation_index is not None else "")
            for i in self.issues
            if i.blocking
        ]
        return "\n".join(lines)


def check_variation_legality(
    fen_partenza: str, mosse_san: list[str]
) -> tuple[bool, str | None, list[str]]:
    """Verifica che ``mosse_san`` sia legale a partire da ``fen_partenza``.

    Returns:
        ``(legale, errore, mosse_uci)``. ``errore`` è None se legale.
    """
    try:
        board = chess.Board(fen_partenza)
    except ValueError as exc:
        return False, f"FEN di partenza non valido: {fen_partenza!r} ({exc})", []

    if not mosse_san:
        return False, "variante senza mosse", []

    uci: list[str] = []
    for san in mosse_san:
        try:
            move = board.parse_san(san)
        except ValueError as exc:
            return (
                False,
                f"mossa illegale {san!r} da {board.fen()}: {exc}",
                uci,
            )
        uci.append(move.uci())
        board.push(move)
    return True, None, uci


def _provided_moves(moments: list[CriticalMoment]) -> dict[str, set[str]]:
    """Per ogni FEN di momento, l'insieme delle mosse fornite dal motore (UCI).

    Le mosse "date" sono la mossa giocata, la migliore e i primi colpi delle linee
    profonde. Serve a distinguere una variante ancorata ai dati da un'estensione.
    """
    provided: dict[str, set[str]] = {}
    for m in moments:
        moves: set[str] = set()
        if m.uci:
            moves.add(m.uci)
        if m.best_move_uci:
            moves.add(m.best_move_uci)
        for line in m.deep_lines:
            if line.move_uci:
                moves.add(line.move_uci)
            moves.update(line.pv_uci[:2])
        provided.setdefault(m.fen_before, set()).update(moves)
    return provided


def validate_report(
    report: CoachReport,
    moments: list[CriticalMoment],
) -> ValidationResult:
    """Valida l'intero report rispetto ai momenti forniti al motore (§6.4)."""
    result = ValidationResult()
    allowed_fens = {m.fen_before for m in moments}
    known_ids = {m.id for m in moments}
    fen_by_id = {m.id: m.fen_before for m in moments}
    provided = _provided_moves(moments)

    # Un report senza momenti quando il motore ne ha forniti è vacuo: lo schema lo
    # accetterebbe (ogni campo ha un default) e verrebbe mostrato come "validato".
    if moments and not report.momenti:
        result.issues.append(
            ValidationIssue(
                kind="missing_moments",
                message="il report non commenta nessuno dei momenti critici forniti",
            )
        )

    for momento in report.momenti:
        if momento.id not in known_ids:
            result.issues.append(
                ValidationIssue(
                    kind="unknown_moment",
                    message=f"momento {momento.id!r} non presente nei dati forniti",
                    moment_id=momento.id,
                )
            )
        elif momento.fen_prima and momento.fen_prima != fen_by_id[momento.id]:
            # id e FEN devono appartenere allo STESSO momento: incrociarli mostrerebbe
            # la scacchiera sbagliata.
            result.issues.append(
                ValidationIssue(
                    kind="mismatched_fen",
                    message=(
                        f"FEN {momento.fen_prima!r} non appartiene al momento "
                        f"{momento.id!r}"
                    ),
                    moment_id=momento.id,
                )
            )
        elif momento.fen_prima and momento.fen_prima not in allowed_fens:
            result.issues.append(
                ValidationIssue(
                    kind="unknown_fen",
                    message=f"FEN non presente nei dati forniti: {momento.fen_prima!r}",
                    moment_id=momento.id,
                )
            )

        for idx, var in enumerate(momento.varianti_citate):
            result.total_variations += 1
            if var.fen_partenza not in allowed_fens:
                result.grounding_failures += 1
                result.issues.append(
                    ValidationIssue(
                        kind="unknown_fen",
                        message=f"fen_partenza della variante non fornito: {var.fen_partenza!r}",
                        moment_id=momento.id,
                        variation_index=idx,
                    )
                )
                continue

            legal, err, uci = check_variation_legality(var.fen_partenza, var.mosse_san)
            if not legal:
                result.issues.append(
                    ValidationIssue(
                        kind="empty_variation" if not var.mosse_san else "illegal_move",
                        message=err or "variante non valida",
                        moment_id=momento.id,
                        variation_index=idx,
                    )
                )
                continue

            result.legal_variations += 1

            # Ancoraggio ai dati: se la prima mossa non è tra quelle fornite dal
            # motore, è un'estensione (§6.2 consente fino a 2 semimosse) → da
            # verificare a Stockfish, non bloccante.
            known = provided.get(var.fen_partenza, set())
            if var.mosse_san and known and uci and uci[0] not in known:
                result.issues.append(
                    ValidationIssue(
                        kind="extension",
                        message=(
                            f"variante {uci} estende le linee del motore da "
                            f"{var.fen_partenza!r}: da verificare a Stockfish"
                        ),
                        moment_id=momento.id,
                        variation_index=idx,
                        blocking=False,
                    )
                )

    return result


def sanitize_llm_response(raw: str) -> str:
    """Pulizia del testo prodotto dall'LLM (tag, fence Markdown, spazi)."""
    import re

    s = raw.strip()
    s = re.sub(r"```[a-zA-Z]*\n?", "", s)
    s = s.replace("```", "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()
