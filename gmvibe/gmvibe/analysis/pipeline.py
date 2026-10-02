"""Pipeline di analisi completa (documentazione.txt §5, §10 FASE 2).

    PGN → parsing+clock → pass veloce → selezione candidati → pass profondo
        → metriche (classificazione, accuratezza per fase) → Complexity Score

Le fasi successive (feature posizionali, tipo d'errore, LLM, UI) si innestano qui.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

import chess

from .. import config as cfg
from ..db import Database
from ..engine.stockfish_runner import StockfishRunner, build_runner
from ..fairplay import require_idle
from ..level import resolve_level
from ..metrics import winpct_from_score, winpct_loss
from ..models import (
    AccuracySummary,
    EndgameVerdict,
    ErrorThresholds,
    GameAnalysis,
    LevelConfig,
    MoveRow,
    OpeningInfo,
    PVLine,
)
from ..sources.opening_explorer import OpeningExplorer
from ..sources.openings import load_opening_book
from ..sources.pgn_loader import parse_pgn_text
from .candidates import is_candidate, select_for_deep_pass
from .complexity import compute_complexity
from .error_taxonomy import ErrorContext, classify_error
from .features import compute_features, feature_delta
from .metrics import classify_move, detect_phase, game_accuracy
from .tablebase import TablebaseProbe, piece_count, white_pov_category
from .time_analysis import analyze_time, cadenza_from_timecontrol


def _mover_pov(score_cp: int | None, mate: int | None, color: str) -> tuple[int | None, int | None]:
    if color == "white":
        return score_cp, mate
    return (
        None if score_cp is None else -score_cp,
        None if mate is None else -mate,
    )


def _line_winpct(line: PVLine, color: str) -> float:
    cp, mate = _mover_pov(line.score_cp, line.mate, color)
    return winpct_from_score(cp, mate)


def _pgn_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _cached_analyse(
    db: Database,
    runner: StockfishRunner,
    board: chess.Board,
    version_key: str,
    *,
    cache_depth: int,
    multipv: int,
    depth: int | None,
    time_limit: float | None,
    root_moves: list[chess.Move] | None,
    refresh: bool,
) -> tuple:
    """Analisi con cache condivisa (§5.D). La chiave include la root move se presente."""
    fen = board.fen()
    root_key = root_moves[0].uci() if root_moves else ""
    if not refresh:
        cached = db.cache_get(fen, cache_depth, multipv, version_key, root_key)
        if cached is not None:
            return cached, True
    ev = runner.analyse_position(
        board, depth=depth, time=time_limit, multipv=multipv, root_moves=root_moves
    )
    db.cache_put(ev.model_copy(update={"depth": cache_depth}), root_key)
    return ev, False


def analyze_game(
    pgn_text: str,
    *,
    profile_name: str | None = None,
    depth: int | None = None,
    time_limit: float | None = None,
    deep_depth: int | None = None,
    deep: bool = True,
    refresh: bool = False,
    db: Database | None = None,
    runner: StockfishRunner | None = None,
    user_id: str | None = None,
    maia_client: object | None = None,
) -> tuple[GameAnalysis, dict]:
    """Analizza una partita (pass veloce + pass profondo sui candidati)."""
    # §8.3: PRIMA di toccare il motore. Nessun percorso (CLI/web/batch/chiamata
    # diretta) puo' analizzare mentre una partita live e' in corso.
    require_idle(action="analisi della partita")
    levels = cfg.load_levels()
    conversion = cfg.load_rating_conversion()

    level_config: LevelConfig | None = None
    thresholds: ErrorThresholds = levels.winpct_thresholds_default
    explorer_token: str | None = None
    if profile_name is not None:
        profile = cfg.load_profile(profile_name)
        level_config = resolve_level(profile, levels, conversion)
        thresholds = level_config.error_thresholds
        # token Lichess (facoltativo) per l'Opening Explorer; mai loggato
        try:
            secrets = cfg.load_secrets(profile_name)
            if secrets.lichess_token is not None:
                explorer_token = secrets.lichess_token.get_secret_value()
        except Exception:
            explorer_token = None

    game = parse_pgn_text(pgn_text)
    book = load_opening_book()

    fast = levels.fast_pass
    use_depth = depth if depth is not None else (fast.depth or None)
    use_time = None if use_depth else (time_limit if time_limit is not None else fast.time)
    fast_cache_depth = use_depth if use_depth is not None else 0

    deep_cfg = levels.deep_pass
    deep_use_depth = deep_depth if deep_depth is not None else deep_cfg.depth
    deep_use_time = None if deep_use_depth else deep_cfg.time
    deep_cache_depth = deep_use_depth if deep_use_depth is not None else 0

    own_db = db is None
    own_runner = runner is None
    database = db or Database()
    engine = runner or build_runner(levels.engine)
    if own_runner:
        engine.__enter__()

    stats = {"cache_hits": 0, "engine_calls": 0, "deep_analyses": 0, "moves": 0}
    rows: list[MoveRow] = []
    fast_best_uci: dict[int, str | None] = {}
    seq_uci: list[str] = []

    try:
        version_key = engine.engine_version_key()
        # Posizione iniziale dalla partita (rispetta SetUp/FEN: puo' iniziare da un finale).
        board = chess.Board(game.start_fen)
        prev_ev, hit = _cached_analyse(
            database, engine, board, version_key, cache_depth=fast_cache_depth,
            multipv=fast.multipv, depth=use_depth, time_limit=use_time,
            root_moves=None, refresh=refresh,
        )
        stats["cache_hits"] += int(hit)
        stats["engine_calls"] += int(not hit)

        for pm in game.moves:
            sequence_uci = list(seq_uci) + [pm.uci]
            eco, opening, _matched = book.match_full(sequence_uci)
            in_book = book.in_book(sequence_uci)

            board_before = board.copy()
            move = chess.Move.from_uci(pm.uci)
            board.push(move)
            board_after = board.copy()

            ev_after, hit_after = _cached_analyse(
                database, engine, board_after, version_key, cache_depth=fast_cache_depth,
                multipv=fast.multipv, depth=use_depth, time_limit=use_time,
                root_moves=None, refresh=refresh,
            )
            stats["cache_hits"] += int(hit_after)
            stats["engine_calls"] += int(not hit_after)

            s_before, m_before = _mover_pov(
                prev_ev.lines[0].score_cp, prev_ev.lines[0].mate, pm.color
            )
            wb = winpct_from_score(s_before, m_before)
            if board_after.is_checkmate():
                wa, s_after, m_after = 100.0, None, 0
            elif board_after.is_game_over():
                wa, s_after, m_after = 50.0, None, None
            else:
                s_after, m_after = _mover_pov(
                    ev_after.lines[0].score_cp, ev_after.lines[0].mate, pm.color
                )
                wa = winpct_from_score(s_after, m_after)

            cand, _reasons = is_candidate(
                winpct_before=wb, winpct_after=wa, clock_delta=pm.clock_delta,
                board_before=board_before, board_after=board_after,
                cfg=levels.candidate_selection,
            )

            row = MoveRow(
                ply=pm.ply, move_number=pm.move_number, color=pm.color, san=pm.san,
                uci=pm.uci, fen_before=pm.fen_before, fen_after=pm.fen_after,
                score_cp=s_after, mate=m_after,
                winpct_before=round(wb, 2), winpct_after=round(wa, 2),
                winpct_loss=round(winpct_loss(wb, wa), 2),
                clock_seconds=pm.clock_seconds, clock_delta=pm.clock_delta,
                opening_name=opening, opening_eco=eco, in_book=in_book, in_cache=hit_after,
                phase=detect_phase(board_before, in_book),
                # le posizioni terminali hanno gia' un verdetto esatto: niente pass profondo
                is_candidate=cand and deep and not board_after.is_game_over(),
                best_move_uci=prev_ev.lines[0].move_uci or None,
                best_move_san=prev_ev.lines[0].move_san,
            )
            fast_best_uci[pm.ply] = prev_ev.lines[0].move_uci or None
            rows.append(row)

            seq_uci = sequence_uci
            prev_ev = ev_after

        # --- Pass profondo sui candidati (§5.D) ---
        if deep:
            # Tetto al costo (§5.D): si approfondiscono prima le mosse col calo di win%
            # maggiore, cosi' una partita caotica non fa esplodere il tempo.
            cand_rows, skipped = select_for_deep_pass(rows, deep_cfg.max_candidates)
            stats["deep_skipped"] = skipped
            for row in cand_rows:
                _deepen_row(
                    row, database, engine, version_key, levels,
                    deep_cache_depth=deep_cache_depth, deep_depth=deep_use_depth,
                    deep_time=deep_use_time, multipv=deep_cfg.multipv,
                    fast_best_uci=fast_best_uci.get(row.ply), refresh=refresh, stats=stats,
                    maia_client=maia_client,
                )

        # --- Classificazione + natura dell'errore + accuratezza (§5.E, §5.G) ---
        _finalize(rows, thresholds, levels.classification, levels.error_taxonomy, fast_best_uci)

        analysis = GameAnalysis(
            headers=game.headers, result=game.result, white=game.white, black=game.black,
            level_config=level_config, rows=rows,
        )
        _summarize(analysis)

        # --- FASE 4: apertura, finale con tablebase, tempo ---
        analysis.opening = _opening_info(rows, levels, stats, token=explorer_token)
        analysis.endgame = _endgame_verdicts(rows, levels, stats)
        cadenza = cadenza_from_timecontrol(game.headers.get("TimeControl"))
        if levels.time_analysis.enabled:
            analysis.time = analyze_time(rows, cadenza, levels.time_analysis)

        stats["moves"] = len(rows)
    finally:
        if own_runner:
            engine.__exit__(None, None, None)
        if own_db:
            database.close()

    return analysis, stats


def _deepen_row(
    row: MoveRow,
    db: Database,
    engine: StockfishRunner,
    version_key: str,
    levels,
    *,
    deep_cache_depth: int,
    deep_depth: int | None,
    deep_time: float | None,
    multipv: int,
    fast_best_uci: str | None,
    refresh: bool,
    stats: dict,
    maia_client: object | None = None,
) -> None:
    """Pass profondo di una mossa candidata: MultiPV=4 sulla posizione prima + la mossa
    giocata via ``root_moves`` (§5.D). Aggiorna la riga con eval profonda e Complexity."""
    color = row.color
    board_before = chess.Board(row.fen_before)
    played = chess.Move.from_uci(row.uci)

    ev_deep, hit = _cached_analyse(
        db, engine, board_before, version_key, cache_depth=deep_cache_depth,
        multipv=multipv, depth=deep_depth, time_limit=deep_time,
        root_moves=None, refresh=refresh,
    )
    stats["cache_hits"] += int(hit)
    stats["engine_calls"] += int(not hit)
    stats["deep_analyses"] += 1

    ev_played, hit2 = _cached_analyse(
        db, engine, board_before, version_key, cache_depth=deep_cache_depth,
        multipv=1, depth=deep_depth, time_limit=deep_time,
        root_moves=[played], refresh=refresh,
    )
    stats["cache_hits"] += int(hit2)
    stats["engine_calls"] += int(not hit2)
    stats["deep_analyses"] += 1

    deep_lines = ev_deep.lines
    if not deep_lines:
        return
    best_line = deep_lines[0]
    best_winpct = _line_winpct(best_line, color)
    played_winpct = _line_winpct(ev_played.lines[0], color) if ev_played.lines else None

    row.deep_lines = deep_lines
    row.deep_depth = ev_deep.depth
    row.best_move_uci = best_line.move_uci or None
    row.best_move_san = best_line.move_san
    row.gap_winpct = round(
        best_winpct - (_line_winpct(deep_lines[1], color) if len(deep_lines) > 1 else best_winpct), 2
    )
    if played_winpct is not None:
        row.winpct_after = round(played_winpct, 2)
        row.winpct_loss = round(best_winpct - played_winpct, 2)
        if ev_played.lines:
            row.score_cp = _mover_pov(
                ev_played.lines[0].score_cp, ev_played.lines[0].mate, color
            )[0]
            row.mate = _mover_pov(
                ev_played.lines[0].score_cp, ev_played.lines[0].mate, color
            )[1]

    # Fattore 5 (Maia, §5.H): probabilita' umana della mossa migliore del motore. Se Maia
    # non e' disponibile il client e' None e il fattore semplicemente non entra.
    human_prob = None
    if maia_client is not None and best_line.move_uci:
        try:
            human_prob = maia_client.human_move_prob(board_before, best_line.move_uci)
        except Exception:  # noqa: BLE001 - Maia e' opzionale: un guasto non ferma l'analisi
            human_prob = None

    row.complexity = compute_complexity(
        [_line_winpct(ln, color) for ln in deep_lines],
        config=levels.complexity, board_before=board_before, best_move=played,
        fast_best_uci=fast_best_uci, deep_best_uci=best_line.move_uci or None,
        human_prob=human_prob,
    )
    _attach_features(row)


def _attach_features(row: MoveRow) -> None:
    """Calcola il fact sheet prima/dopo e il delta (§5.F), se non gia' fatto."""
    if row.features_before is not None:
        return
    board_before = chess.Board(row.fen_before)
    board_after = chess.Board(row.fen_after)
    fb = compute_features(board_before)
    fa = compute_features(board_after)
    row.features_before = fb
    row.features_delta = feature_delta(fb, fa)


def _finalize(
    rows: list[MoveRow],
    thresholds: ErrorThresholds,
    classification,
    error_taxonomy,
    fast_best_uci: dict[int, str | None],
) -> None:
    for row in rows:
        if row.winpct_before is None or row.winpct_after is None or row.winpct_loss is None:
            continue
        row.label = classify_move(
            row.winpct_loss, thresholds,
            winpct_before=row.winpct_before, winpct_after=row.winpct_after,
            classification=classification,
        )
        if row.label == "ok":
            continue
        # ogni errore ha una natura: feature (se mancanti) + tassonomia (§5.G)
        _attach_features(row)
        ctx = ErrorContext(
            winpct_before=row.winpct_before,
            winpct_after=row.winpct_after,
            phase=row.phase or "mediogioco",
            board_before=chess.Board(row.fen_before),
            board_after=chess.Board(row.fen_after),
            played_move=chess.Move.from_uci(row.uci),
            best_pv_uci=row.deep_lines[0].pv_uci if row.deep_lines else [],
            low_depth_best_uci=fast_best_uci.get(row.ply),
            deep_best_uci=row.best_move_uci,
            clock_seconds=row.clock_seconds,
            clock_delta=row.clock_delta,
            features_before=row.features_before,
            features_delta=row.features_delta,
        )
        row.error_label = classify_error(ctx, error_taxonomy)


def _opening_info(rows: list[MoveRow], levels, stats: dict,
                  token: str | None = None) -> OpeningInfo | None:
    """Nome/ECO dell'apertura e uscita dalla teoria (§5.A, §5.L).

    L'uscita dalla teoria e' calcolata in modo DETERMINISTICO dal dataset apertura
    (prima mossa che non e' piu' esattamente in teoria). L'Opening Explorer di Lichess
    arricchisce con le statistiche reali, ma oggi richiede un token: senza token si
    salta e resta comunque l'uscita dalla teoria offline.
    """
    if not rows:
        return None
    last_eco = next((r.opening_eco for r in reversed(rows) if r.opening_eco), None)
    last_name = next((r.opening_name for r in reversed(rows) if r.opening_name), None)
    info = OpeningInfo(eco=last_eco, name=last_name)

    # Uscita dalla teoria dal dataset apertura (offline, deterministico).
    was_in_book = False
    for row in rows:
        if row.in_book:
            was_in_book = True
            continue
        if was_in_book:
            info.exit_ply = row.ply
            info.exit_move_san = row.san
            info.exit_move_uci = row.uci
            break

    # Enrichment opzionale con l'Opening Explorer (richiede token).
    cfg = levels.opening_explorer
    if info.exit_ply is None or not cfg.enabled or not cfg.allow_network:
        return info
    explorer = OpeningExplorer(cfg, token=token)
    board = chess.Board()
    for row in rows[:info.exit_ply]:
        board.push(chess.Move.from_uci(row.uci))
    st = explorer.query(board)
    stats["explorer_calls"] = stats.get("explorer_calls", 0) + 1
    if st is not None:
        info.explorer_stats = st
    return info


def _endgame_verdicts(rows: list[MoveRow], levels, stats: dict) -> list[EndgameVerdict]:
    """Verdetti esatti sui finali con pochi pezzi (§5.K). Mai dedotti da Claude."""
    cfg = levels.tablebase
    if not cfg.enabled:
        return []
    probe = TablebaseProbe(cfg)
    out: list[EndgameVerdict] = []
    for row in rows:
        after = chess.Board(row.fen_after)
        if piece_count(after) > cfg.lichess_max_pieces:
            continue
        before = chess.Board(row.fen_before)
        vb = probe.probe(before)
        va = probe.probe(after)
        stats["tablebase_calls"] = stats.get("tablebase_calls", 0) + int(vb is not None) + int(va is not None)
        if vb is None and va is None:
            continue
        out.append(EndgameVerdict(
            ply=row.ply, san=row.san, fen_before=row.fen_before, fen_after=row.fen_after,
            verdict_before=vb, verdict_after=va,
            result_changed=white_pov_category(vb, before) != white_pov_category(va, after),
        ))
    return out


def _summarize(analysis: GameAnalysis) -> None:
    """Calcola accuratezza totale/per fase e i conteggi per colore."""
    losses = {"white": [], "black": []}
    losses_phase: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"white": [], "black": []})
    counts = {
        "white": {"imprecisione": 0, "errore": 0, "svarione": 0},
        "black": {"imprecisione": 0, "errore": 0, "svarione": 0},
    }
    for row in analysis.rows:
        if row.winpct_loss is None:
            continue
        losses[row.color].append(row.winpct_loss)
        if row.phase is not None:
            losses_phase[row.phase][row.color].append(row.winpct_loss)
        if row.label != "ok":
            counts[row.color][row.label] += 1

    analysis.accuracy = AccuracySummary(
        bianco=game_accuracy(losses["white"]),
        nero=game_accuracy(losses["black"]),
    )
    analysis.accuracy_by_phase = {
        phase: AccuracySummary(
            bianco=game_accuracy(v["white"]), nero=game_accuracy(v["black"])
        )
        for phase, v in losses_phase.items()
    }
    analysis.counts = counts
