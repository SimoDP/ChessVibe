"""Metriche di base (documentazione.txt §5.E).

Si usa la **win probability** invece dei centipedoni: un -1.5 in una posizione +8 non e'
un errore reale, in una posizione 0.00 si'.

    win%(cp) = 50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)

Questo modulo e' l'unica parte di FASE 2 anticipata in FASE 1, perche' la formula win% e'
un criterio di accettazione esplicito della FASE 1 (§10).
"""

from __future__ import annotations

import math

# Costante usata da Lichess per la win probability.
_WINPCT_K = 0.00368208


def winpct(cp: int | float) -> float:
    """Win probability (0-100) da una valutazione in centipedoni, POV di chi muove."""
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-_WINPCT_K * float(cp))) - 1.0)


def winpct_from_score(score_cp: int | None, mate: int | None) -> float:
    """Win probability tenendo conto dei matti.

    ``mate`` e' il numero di mosse al matto dal POV di chi muove: positivo = chi muove
    matta l'avversario (win% 100), negativo = chi muove viene mattato (win% 0).
    """
    if mate is not None:
        return 100.0 if mate > 0 else 0.0
    if score_cp is None:
        return 50.0
    return winpct(score_cp)


def winpct_loss(winpct_before: float, winpct_after: float) -> float:
    """Perdita della mossa = win%(prima) - win%(dopo), POV di chi muove (§5.E).

    Valori negativi (una mossa che migliora) vengono riportati cosi' come sono; la
    classificazione in FASE 2 ignorera' comunque le mosse che non peggiorano.
    """
    return winpct_before - winpct_after


def accuracy_from_loss(loss_winpct: float) -> float:
    """Accuratezza della mossa dalla perdita in win% (formula Lichess, §5.E).

        accuracy = 103.1668 * exp(-0.04354 * perdita_win%) - 3.1669
    """
    loss = max(0.0, float(loss_winpct))
    return 103.1668 * math.exp(-0.04354 * loss) - 3.1669
