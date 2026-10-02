"""Calibrazione automatica dal rating (documentazione.txt §4.3).

``resolve_level`` e' una FUNZIONE PURA: stesso input -> stesso ``LevelConfig``, senza I/O,
senza randomness, senza stato globale. Tutto cio' che dipende dal livello passa da qui.

Test obbligatori in tests/test_level.py:
- monotonia delle soglie al crescere del rating
- casi limite 1100, 1900, 2200, 2600, 3000 e valori invalidi
- determinismo
- priorita' del rating Lichess reale sulla conversione
- flag ``estimated`` / ``extrapolated``
"""

from __future__ import annotations

from .models import (
    ErrorThresholds,
    LevelAnchor,
    LevelConfig,
    LevelsConfig,
    PlayerProfile,
    RatingConversion,
)


def to_lichess_scale(
    rating: int,
    tipo: str,
    cadenza: str,
    conversion: RatingConversion,
) -> tuple[int, bool]:
    """Converte un rating dichiarato in scala Lichess.

    Ritorna ``(rating_lichess, estimated)``. Se ``tipo == "lichess"`` il rating e' gia'
    sulla scala giusta e ``estimated`` e' False. Altrimenti si applica il delta di
    ``rating_conversion.yaml`` (stima approssimativa) e ``estimated`` e' True.
    """
    if tipo == "lichess":
        return _clamp(rating, conversion), False

    table = conversion.tables.get(tipo)
    if not table:
        # Tipo senza tabella: nessuna conversione nota, resta una stima.
        return _clamp(rating, conversion), True

    entry = table.get(cadenza) or table.get(conversion.default_cadenza)
    delta = entry.delta if entry is not None else 0
    return _clamp(rating + delta, conversion), True


def _clamp(rating: int, conversion: RatingConversion) -> int:
    return max(conversion.clamp.min, min(conversion.clamp.max, int(rating)))


def nearest_anchor(rating: int, anchors: list[LevelAnchor]) -> LevelAnchor:
    """Anchor piu' vicino al rating. A parita' di distanza vince l'anchor piu' alto."""
    # max() con chiave: (-distanza, rating) -> distanza minore, poi rating maggiore.
    return max(anchors, key=lambda a: (-abs(a.rating - rating), a.rating))


def _interp_value(rating: int, anchors: list[LevelAnchor], getter) -> float:
    """Interpolazione lineare di un parametro tra gli anchor bracketing.

    Fuori range: clamp all'anchor estremo (il flag ``extrapolated`` lo segnala a parte).
    """
    if rating <= anchors[0].rating:
        return float(getter(anchors[0]))
    if rating >= anchors[-1].rating:
        return float(getter(anchors[-1]))
    for lo, hi in zip(anchors, anchors[1:]):
        if lo.rating <= rating <= hi.rating:
            span = hi.rating - lo.rating
            if span == 0:
                return float(getter(hi))
            t = (rating - lo.rating) / span
            return float(getter(lo)) + t * (float(getter(hi)) - float(getter(lo)))
    return float(getter(anchors[-1]))  # pragma: no cover - coperto dai rami sopra


def _select_maia(
    rating_lichess: int,
    levels: LevelsConfig,
    maia2_available: bool,
    maia1_available: bool,
) -> tuple[str | None, str, bool]:
    """Sceglie il modello Maia (§4.3.2). Ritorna ``(nome, kind, extrapolated)``.

    - Maia-2: usa direttamente il rating in scala Lichess (nessun bucket) -> preferito.
    - Maia originale: bucket piu' vicino tra quelli disponibili; fuori range si usa
      il bucket estremo e si marca ``extrapolated``.
    - Nessun Maia installato / disabilitato: funzione disattivata (``none``).
    """
    if not levels.maia.enabled or not (maia2_available or maia1_available):
        return None, "none", False

    if maia2_available:
        # Maia-2 e' condizionato sul rating: nessun bucket, nessuna extrapolazione qui.
        # La copertura reale del range va verificata in FASE 8.
        return "maia2", "maia2", False

    buckets = sorted(levels.maia.maia1_buckets)
    if not buckets:  # pragma: no cover - config di default ha sempre bucket
        return None, "none", False

    # Bucket piu' vicino (multiplo di 100 disponibile), a parita' il piu' alto.
    bucket = max(buckets, key=lambda b: (-abs(b - rating_lichess), b))
    extrapolated = rating_lichess < buckets[0] or rating_lichess > buckets[-1]
    return f"maia-{bucket}", "maia1", extrapolated


def resolve_level(
    profile: PlayerProfile,
    levels: LevelsConfig,
    conversion: RatingConversion,
    *,
    maia2_available: bool = False,
    maia1_available: bool = False,
    declared_lichess_rating: int | None = None,
) -> LevelConfig:
    """Calcola il ``LevelConfig`` dal profilo (documentazione.txt §4.3).

    ``declared_lichess_rating``: se l'utente fornisce un rating Lichess REALE per la
    cadenza scelta, questo ha la priorita' sulla conversione (§4.3.1).
    """
    anchors = levels.sorted_anchors()
    if not anchors:  # pragma: no cover - levels.yaml ha sempre anchor
        raise ValueError("levels.yaml non contiene punti di ancoraggio")

    if declared_lichess_rating is not None and profile.rating_tipo == "lichess":
        rating_lichess = _clamp(declared_lichess_rating, conversion)
        source, estimated = "declared_lichess", False
    else:
        rating_lichess, estimated = to_lichess_scale(
            profile.rating, profile.rating_tipo, profile.cadenza_preferita, conversion
        )
        source = "declared_lichess" if profile.rating_tipo == "lichess" else "converted"

    extrapolated = rating_lichess < anchors[0].rating or rating_lichess > anchors[-1].rating

    # Arrotondamento a 2 decimali: valori puliti nel report e riproducibilita' stabile.
    error_thresholds = ErrorThresholds(
        imprecisione=round(_interp_value(rating_lichess, anchors, lambda a: a.error_thresholds.imprecisione), 2),
        errore=round(_interp_value(rating_lichess, anchors, lambda a: a.error_thresholds.errore), 2),
        svarione=round(_interp_value(rating_lichess, anchors, lambda a: a.error_thresholds.svarione), 2),
    )
    natural_move_prob = _interp_value(rating_lichess, anchors, lambda a: a.natural_move_prob)
    theory_depth_f = _interp_value(rating_lichess, anchors, lambda a: a.theory_depth)
    critical_moments_f = _interp_value(rating_lichess, anchors, lambda a: a.critical_moments)

    # Arrotondamento deterministico degli interi (evita sorprese di banker's rounding).
    theory_depth = int(theory_depth_f + 0.5)
    critical_moments = int(critical_moments_f + 0.5)

    descriptor = nearest_anchor(rating_lichess, anchors).descrittore_livello

    maia_model, maia_kind, maia_extrapolated = _select_maia(
        rating_lichess, levels, maia2_available, maia1_available
    )
    extrapolated = extrapolated or maia_extrapolated

    calibration_line = (
        f"rating {profile.rating} ({profile.rating_tipo}, {profile.cadenza_preferita}) "
        f"-> scala Lichess {rating_lichess} "
        f"[{'Lichess dichiarato' if source == 'declared_lichess' else 'convertito'}], "
        f"livello: {descriptor}, Maia: {maia_model or 'disattivato'}"
        + (", stima conservativa (fuori range)" if extrapolated else "")
    )

    return LevelConfig(
        rating_input=profile.rating,
        rating_tipo=profile.rating_tipo,
        cadenza=profile.cadenza_preferita,
        rating_lichess=rating_lichess,
        rating_lichess_source=source,
        estimated=estimated,
        extrapolated=extrapolated,
        error_thresholds=error_thresholds,
        critical_moments=critical_moments,
        natural_move_prob=round(natural_move_prob, 4),
        theory_depth=theory_depth,
        descrittore_livello=descriptor,
        maia_model=maia_model,
        maia_kind=maia_kind,
        calibration_line=calibration_line,
    )
