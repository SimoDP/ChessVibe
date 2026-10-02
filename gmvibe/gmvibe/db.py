"""SQLite: cache condivisa del motore + dati utente (documentazione.txt §3, §4.1, §5.D).

La cache delle analisi del motore e' CONDIVISA fra tutti i profili: una posizione
(``fen``, ``depth``, ``multipv``, ``versione_motore``) vale per tutti. I dati utente
(games/move_rows) hanno invece una colonna ``user_id``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .models import EngineEval, PVLine, WDL

_SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_cache (
    fen            TEXT NOT NULL,
    depth          INTEGER NOT NULL,
    multipv        INTEGER NOT NULL,
    engine_version TEXT NOT NULL,
    root_move      TEXT NOT NULL DEFAULT '',
    engine_name    TEXT NOT NULL,
    turn           TEXT NOT NULL,
    lines_json     TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (fen, depth, multipv, engine_version, root_move)
);

CREATE TABLE IF NOT EXISTS games (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT NOT NULL,
    pgn_hash          TEXT NOT NULL,
    result            TEXT,
    analyzed_at       TEXT,
    level_config_json TEXT
);

CREATE TABLE IF NOT EXISTS move_rows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply            INTEGER,
    san            TEXT,
    uci            TEXT,
    fen_before     TEXT,
    fen_after      TEXT,
    score_cp       INTEGER,
    mate           INTEGER,
    winpct_before  REAL,
    winpct_after   REAL,
    clock_seconds  REAL,
    opening_name   TEXT
);

-- Analisi complete (FASE 6): JSON integrale di GameAnalysis + report del coach.
-- Le tabelle games/move_rows restano per le statistiche cross-partita (FASE 7); qui
-- si conserva tutto ciò che serve alla UI di review browser (§9), senza ricampionare.
CREATE TABLE IF NOT EXISTS analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    pgn_hash        TEXT NOT NULL,
    white           TEXT,
    black           TEXT,
    result          TEXT,
    analysis_json   TEXT NOT NULL,
    coach_json      TEXT,
    coach_meta_json TEXT,
    created_at      TEXT NOT NULL
);
-- Dedup della ri-analisi: una partita (user_id, pgn_hash) ha una sola riga in ``analyses``.
CREATE INDEX IF NOT EXISTS idx_analyses_user_hash ON analyses(user_id, pgn_hash);

-- PGN grezzo importato (FASE 7, §8.1): conserva il testo originale (con clock) per
-- ri-analisi/export/dedup senza tornare in rete. Una riga per partita importata.
CREATE TABLE IF NOT EXISTS imported_games (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    pgn_hash    TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'pgn',   -- pgn | lichess | chesscom
    white       TEXT,
    black       TEXT,
    result      TEXT,
    cadenza     TEXT,
    pgn_text    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
-- Una sola riga per (user_id, pgn_hash): ri-analizzare non deve duplicare il PGN grezzo.
CREATE INDEX IF NOT EXISTS idx_imported_user_hash ON imported_games(user_id, pgn_hash);

-- Ogni errore etichettato (§7): "memorizza in SQLite ogni errore con la sua etichetta".
-- game_id punta all'analisi completa (analyses.id). recurrence_prior = n. partite
-- PRECEDENTI con lo stesso error_label (il riquadro "già comparso in X partite").
CREATE TABLE IF NOT EXISTS errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    game_id         INTEGER REFERENCES analyses(id) ON DELETE CASCADE,
    ply             INTEGER,
    move_number     INTEGER,
    color           TEXT,
    phase           TEXT,
    label           TEXT,
    error_label     TEXT,
    san             TEXT,
    fen_before      TEXT,
    opening_name    TEXT,
    opening_eco     TEXT,
    cadenza         TEXT,
    clock_seconds   REAL,
    winpct_loss     REAL,
    recurrence_prior INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_errors_user_label ON errors(user_id, error_label);
CREATE INDEX IF NOT EXISTS idx_errors_user_game ON errors(user_id, game_id);

-- Puzzle generati dagli errori (§7.1). La soluzione è la mossa del motore.
CREATE TABLE IF NOT EXISTS puzzles (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT NOT NULL,
    game_id            INTEGER REFERENCES analyses(id) ON DELETE SET NULL,
    ply                INTEGER,
    fen                TEXT NOT NULL,
    solution_uci       TEXT NOT NULL,
    solution_san       TEXT,
    alternatives_json  TEXT NOT NULL DEFAULT '[]',
    theme              TEXT,
    explanation        TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_puzzles_user ON puzzles(user_id);

-- Stato del ripasso dilazionato (Leitner, §7.1): una riga per puzzle.
CREATE TABLE IF NOT EXISTS puzzle_reviews (
    puzzle_id        INTEGER PRIMARY KEY REFERENCES puzzles(id) ON DELETE CASCADE,
    box              INTEGER NOT NULL DEFAULT 1,
    due_at           TEXT,
    last_reviewed_at TEXT,
    times_seen       INTEGER NOT NULL DEFAULT 0,
    times_correct    INTEGER NOT NULL DEFAULT 0
);

-- Contabilità del layer LLM (§6.5): ogni riga ha user_id come le altre tabelle utente.
CREATE TABLE IF NOT EXISTS llm_usage (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                     TEXT NOT NULL,
    game_id                     INTEGER REFERENCES games(id) ON DELETE SET NULL,
    model                       TEXT NOT NULL,
    input_tokens                INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd                    REAL NOT NULL DEFAULT 0.0,
    created_at                  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Gestore del DB SQLite (cache motore + dati utente)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else paths.default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: il server web (FASE 6) usa una connessione condivisa
        # fra il thread dei job e i thread delle richieste. Le scritture sono serializzate
        # da ``_write_lock``; le letture sfruttano il WAL (lettori concorrenti).
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._write_lock = threading.RLock()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Migrazioni leggere. La cache motore e' usa-e-getta: se lo schema e' vecchio
        (manca ``root_move``) la si ricrea invece di perderci tempo."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(engine_cache)")}
        if cols and "root_move" not in cols:
            self.conn.execute("DROP TABLE engine_cache")
            self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Cache motore (condivisa)
    # ------------------------------------------------------------------ #
    def cache_get(
        self, fen: str, depth: int, multipv: int, engine_version: str, root_move: str = ""
    ) -> EngineEval | None:
        row = self.conn.execute(
            "SELECT * FROM engine_cache WHERE fen=? AND depth=? AND multipv=? "
            "AND engine_version=? AND root_move=?",
            (fen, depth, multipv, engine_version, root_move),
        ).fetchone()
        if row is None:
            return None
        lines = [PVLine(**ln) for ln in json.loads(row["lines_json"])]
        return EngineEval(
            fen=row["fen"],
            depth=row["depth"],
            multipv=row["multipv"],
            engine_name=row["engine_name"],
            engine_version=row["engine_version"],
            turn=row["turn"],
            lines=lines,
        )

    def cache_put(self, ev: EngineEval, root_move: str = "") -> None:
        lines_json = json.dumps([ln.model_dump() for ln in ev.lines])
        with self._write_lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO engine_cache
                    (fen, depth, multipv, engine_version, root_move, engine_name, turn,
                     lines_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ev.fen, ev.depth, ev.multipv, ev.engine_version, root_move, ev.engine_name,
                 ev.turn, lines_json, _now()),
            )
            self.conn.commit()

    def cache_stats(self) -> dict[str, int]:
        total = self.conn.execute("SELECT COUNT(*) AS c FROM engine_cache").fetchone()["c"]
        return {"entries": int(total)}

    # ------------------------------------------------------------------ #
    # Dati utente
    # ------------------------------------------------------------------ #
    def save_game(self, user_id: str, pgn_hash: str, result: str,
                  level_config_json: str, rows: list[dict]) -> int:
        with self._write_lock:
            cur = self.conn.execute(
                "INSERT INTO games (user_id, pgn_hash, result, analyzed_at, level_config_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, pgn_hash, result, _now(), level_config_json),
            )
            game_id = int(cur.lastrowid)
            self.conn.executemany(
                """
                INSERT INTO move_rows
                    (game_id, ply, san, uci, fen_before, fen_after, score_cp, mate,
                     winpct_before, winpct_after, clock_seconds, opening_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        game_id, r.get("ply"), r.get("san"), r.get("uci"),
                        r.get("fen_before"), r.get("fen_after"), r.get("score_cp"),
                        r.get("mate"), r.get("winpct_before"), r.get("winpct_after"),
                        r.get("clock_seconds"), r.get("opening_name"),
                    )
                    for r in rows
                ],
            )
            self.conn.commit()
            return game_id

    # ------------------------------------------------------------------ #
    # Analisi complete (FASE 6): JSON integrale per la UI di review (§9)
    # ------------------------------------------------------------------ #
    def save_analysis(
        self,
        user_id: str,
        pgn_hash: str,
        *,
        white: str | None,
        black: str | None,
        result: str | None,
        analysis_json: str,
    ) -> int:
        """Salva un'analisi completa (JSON serializzato) e ritorna l'id."""
        with self._write_lock:
            cur = self.conn.execute(
                """
                INSERT INTO analyses
                    (user_id, pgn_hash, white, black, result, analysis_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, pgn_hash, white, black, result, analysis_json, _now()),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def get_analysis(self, analysis_id: int) -> dict | None:
        """Riga dell'analisi (dict) o None: contiene ``analysis_json``/``coach_json``."""
        row = self.conn.execute(
            "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
        ).fetchone()
        return dict(row) if row else None

    def find_analysis_by_hash(self, user_id: str, pgn_hash: str) -> dict | None:
        """Analisi già salvata per (user_id, pgn_hash), o None.

        Serve a rendere idempotente la persistenza: ri-analizzare la stessa partita
        riusa la riga esistente invece di crearne una nuova (niente analisi né puzzle
        duplicati). In caso di storici duplicati si prende la riga più recente.
        """
        row = self.conn.execute(
            "SELECT * FROM analyses WHERE user_id = ? AND pgn_hash = ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, pgn_hash),
        ).fetchone()
        return dict(row) if row else None

    def analysis_json(self, analysis_id: int) -> str | None:
        """Solo il JSON dell'analisi (per la UI), o None."""
        row = self.conn.execute(
            "SELECT analysis_json FROM analyses WHERE id = ?", (analysis_id,)
        ).fetchone()
        return row["analysis_json"] if row else None

    def update_analysis_json(self, analysis_id: int, analysis_json: str) -> None:
        """Riscrive il JSON di un'analisi (usato per il campo cross-partita §7)."""
        with self._write_lock:
            self.conn.execute(
                "UPDATE analyses SET analysis_json = ? WHERE id = ?",
                (analysis_json, analysis_id),
            )
            self.conn.commit()

    def update_analysis(
        self,
        analysis_id: int,
        *,
        white: str | None,
        black: str | None,
        result: str | None,
        analysis_json: str,
    ) -> None:
        """Riscrive un'analisi esistente (ri-analisi): aggiorna l'esito e il JSON.

        Il report del Maestro viene azzerato: si riferiva all'analisi precedente, che
        può essere cambiata (profondità/cadenza diverse). Va rigenerato.
        """
        with self._write_lock:
            self.conn.execute(
                "UPDATE analyses SET white = ?, black = ?, result = ?, analysis_json = ?, "
                "coach_json = NULL, coach_meta_json = NULL, created_at = ? WHERE id = ?",
                (white, black, result, analysis_json, _now(), analysis_id),
            )
            self.conn.commit()

    def save_coach(self, analysis_id: int, report_json: str, meta_json: str) -> None:
        """Salva (o sostituisce) il report del Maestro virtuale per un'analisi."""
        with self._write_lock:
            self.conn.execute(
                "UPDATE analyses SET coach_json = ?, coach_meta_json = ? WHERE id = ?",
                (report_json, meta_json, analysis_id),
            )
            self.conn.commit()

    def list_analyses(self, user_id: str | None = None) -> list[dict]:
        """Elenco sintetico delle analisi salvate (più recenti per prime)."""
        if user_id is None:
            rows = self.conn.execute(
                "SELECT id, user_id, white, black, result, created_at, "
                "(coach_json IS NOT NULL) AS has_coach FROM analyses "
                "ORDER BY id DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, user_id, white, black, result, created_at, "
                "(coach_json IS NOT NULL) AS has_coach FROM analyses "
                "WHERE user_id = ? ORDER BY id DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Cross-partita (FASE 7): PGN importati, errori etichettati, puzzle
    # ------------------------------------------------------------------ #
    def save_imported_game(
        self,
        user_id: str,
        pgn_hash: str,
        *,
        source: str,
        white: str | None,
        black: str | None,
        result: str | None,
        cadenza: str | None,
        pgn_text: str,
    ) -> int:
        """Conserva una partita importata (PGN grezzo + hash per dedup)."""
        with self._write_lock:
            cur = self.conn.execute(
                """
                INSERT INTO imported_games
                    (user_id, pgn_hash, source, white, black, result, cadenza, pgn_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, pgn_hash, source, white, black, result, cadenza, pgn_text, _now()),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def find_imported_by_hash(self, user_id: str, pgn_hash: str) -> dict | None:
        """Riga della partita importata con quell'hash (per saltare i duplicati), o None."""
        row = self.conn.execute(
            "SELECT * FROM imported_games WHERE user_id = ? AND pgn_hash = ? LIMIT 1",
            (user_id, pgn_hash),
        ).fetchone()
        return dict(row) if row else None

    def save_errors(self, user_id: str, game_id: int, errors: list[dict]) -> int:
        """Salva gli errori etichettati di una partita. Ritorna quanti ne ha scritti."""
        if not errors:
            return 0
        stamp = _now()
        with self._write_lock:
            self.conn.executemany(
                """
                INSERT INTO errors
                    (user_id, game_id, ply, move_number, color, phase, label, error_label,
                     san, fen_before, opening_name, opening_eco, cadenza, clock_seconds,
                     winpct_loss, recurrence_prior, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        user_id, game_id, e.get("ply"), e.get("move_number"), e.get("color"),
                        e.get("phase"), e.get("label"), e.get("error_label"), e.get("san"),
                        e.get("fen_before"), e.get("opening_name"), e.get("opening_eco"),
                        e.get("cadenza"), e.get("clock_seconds"), e.get("winpct_loss"),
                        int(e.get("recurrence_prior", 0)), stamp,
                    )
                    for e in errors
                ],
            )
            self.conn.commit()
        return len(errors)

    def delete_errors_for_game(self, user_id: str, game_id: int) -> int:
        """Elimina gli errori etichettati di una partita (ri-analisi). Ritorna quanti."""
        with self._write_lock:
            cur = self.conn.execute(
                "DELETE FROM errors WHERE user_id = ? AND game_id = ?", (user_id, game_id)
            )
            self.conn.commit()
            return cur.rowcount

    def list_errors(
        self, user_id: str, *, last_games: int | None = None
    ) -> list[dict]:
        """Errori etichettati dell'utente, opzionalmente limitati alle ultime N partite."""
        if last_games is None:
            rows = self.conn.execute(
                "SELECT * FROM errors WHERE user_id = ? ORDER BY game_id, ply",
                (user_id,),
            ).fetchall()
        else:
            ids = [g["id"] for g in self.list_analyses(user_id)[:last_games]]
            if not ids:
                return []
            placeholders = ",".join("?" * len(ids))
            rows = self.conn.execute(
                f"SELECT * FROM errors WHERE user_id = ? AND game_id IN ({placeholders}) "
                "ORDER BY game_id, ply",
                (user_id, *ids),
            ).fetchall()
        return [dict(r) for r in rows]

    def error_recurrence(self, user_id: str, error_label: str, before_game_id: int) -> int:
        """N. di partite **precedenti** (id < before_game_id) con lo stesso error_label.

        Serve al riquadro §7 "questo errore è già comparso in X partite precedenti".
        """
        if not error_label:
            return 0
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT game_id) AS c FROM errors "
            "WHERE user_id = ? AND error_label = ? AND game_id < ?",
            (user_id, error_label, before_game_id),
        ).fetchone()
        return int(row["c"])

    # ------------------------------------------------------------------ #
    # Puzzle + ripasso dilazionato (§7.1)
    # ------------------------------------------------------------------ #
    def save_puzzle(self, puzzle, review: dict | None = None) -> int:
        """Salva un :class:`~gmvibe.models.Puzzle` e ne crea lo stato di ripasso.

        ``review`` (facoltativo) è lo stato Leitner da ripristinare quando lo stesso
        puzzle viene ricreato da una ri-analisi: senza di esso si parte dal box 1.
        """
        with self._write_lock:
            cur = self.conn.execute(
                """
                INSERT INTO puzzles
                    (user_id, game_id, ply, fen, solution_uci, solution_san,
                     alternatives_json, theme, explanation, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    puzzle.user_id, puzzle.game_id, puzzle.ply, puzzle.fen,
                    puzzle.solution_uci, puzzle.solution_san,
                    json.dumps(puzzle.alternatives, ensure_ascii=False),
                    puzzle.theme, puzzle.explanation, _now(),
                ),
            )
            puzzle_id = int(cur.lastrowid)
            rv = review or {}
            self.conn.execute(
                "INSERT INTO puzzle_reviews "
                "(puzzle_id, box, due_at, last_reviewed_at, times_seen, times_correct) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    puzzle_id,
                    int(rv.get("box") or 1),
                    rv.get("due_at") or _now(),
                    rv.get("last_reviewed_at"),
                    int(rv.get("times_seen") or 0),
                    int(rv.get("times_correct") or 0),
                ),
            )
            self.conn.commit()
            return puzzle_id

    def delete_puzzles_for_game(self, user_id: str, game_id: int) -> list[dict]:
        """Elimina i puzzle di una partita restituendone lo stato di ripasso.

        Serve alla ri-analisi: si cancellano i puzzle vecchi (e, per CASCADE, i loro
        ripassi) ma si conserva ``(fen, solution_uci) -> stato Leitner`` così che i
        puzzle identici rigenerati mantengano il progresso dell'utente.
        """
        with self._write_lock:
            rows = self.conn.execute(
                "SELECT p.id, p.fen, p.solution_uci, r.box, r.due_at, r.last_reviewed_at, "
                "r.times_seen, r.times_correct FROM puzzles p "
                "LEFT JOIN puzzle_reviews r ON r.puzzle_id = p.id "
                "WHERE p.user_id = ? AND p.game_id = ?",
                (user_id, game_id),
            ).fetchall()
            self.conn.execute(
                "DELETE FROM puzzles WHERE user_id = ? AND game_id = ?", (user_id, game_id)
            )
            self.conn.commit()
            return [dict(r) for r in rows]

    def get_puzzle(self, puzzle_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT p.*, r.box, r.due_at, r.times_seen, r.times_correct, r.last_reviewed_at "
            "FROM puzzles p LEFT JOIN puzzle_reviews r ON r.puzzle_id = p.id "
            "WHERE p.id = ?",
            (puzzle_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_puzzles(self, user_id: str, *, due_before: str | None = None) -> list[dict]:
        """Puzzle dell'utente (col loro stato SRS). Con ``due_before`` solo i dovuti."""
        sql = (
            "SELECT p.*, r.box, r.due_at, r.times_seen, r.times_correct, r.last_reviewed_at "
            "FROM puzzles p LEFT JOIN puzzle_reviews r ON r.puzzle_id = p.id "
            "WHERE p.user_id = ?"
        )
        params: list = [user_id]
        if due_before is not None:
            sql += " AND (r.due_at IS NULL OR r.due_at <= ?)"
            params.append(due_before)
        sql += " ORDER BY (r.due_at IS NULL), r.due_at, p.id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_review(self, puzzle_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM puzzle_reviews WHERE puzzle_id = ?", (puzzle_id,)
        ).fetchone()
        return dict(row) if row else None

    def apply_review(
        self, puzzle_id: int, *, box: int, due_at: str, correct: bool
    ) -> None:
        """Aggiorna lo stato Leitner di un puzzle dopo un ripasso."""
        with self._write_lock:
            self.conn.execute(
                """
                UPDATE puzzle_reviews
                   SET box = ?, due_at = ?, last_reviewed_at = ?,
                       times_seen = times_seen + 1,
                       times_correct = times_correct + ?
                 WHERE puzzle_id = ?
                """,
                (box, due_at, _now(), 1 if correct else 0, puzzle_id),
            )
            self.conn.commit()

    # ------------------------------------------------------------------ #
    # Costo LLM (§6.5): "log del costo in token per partita"
    # ------------------------------------------------------------------ #
    def save_llm_usage(self, user_id: str, usage, game_id: int | None = None) -> int:
        """Salva una riga di contabilità token/costo per il layer LLM."""
        with self._write_lock:
            cur = self.conn.execute(
                """
                INSERT INTO llm_usage
                    (user_id, game_id, model, input_tokens, output_tokens,
                     cache_read_input_tokens, cache_creation_input_tokens, cost_usd, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, game_id, usage.model, usage.input_tokens, usage.output_tokens,
                    usage.cache_read_input_tokens, usage.cache_creation_input_tokens,
                    usage.cost_usd, _now(),
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def total_llm_usage(self, user_id: str) -> dict[str, float]:
        """Somma token e costo per un utente (per il report cross-partita)."""
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cache_read_input_tokens), 0) AS cache_read_input_tokens,
                   COALESCE(SUM(cache_creation_input_tokens), 0) AS cache_creation_input_tokens,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd
            FROM llm_usage WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        return {k: (float(row[k]) if k == "cost_usd" else int(row[k])) for k in row.keys()}
