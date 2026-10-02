"""Applicazione FastAPI della UI di review (documentazione.txt §9, §10 FASE 6).

Espone una piccola API JSON e serve la pagina statica con Chessground. Il lavoro
pesante è delegato a :class:`~gmvibe.web.jobs.JobRegistry` (thread) e il browser fa
polling: nessuna richiesta HTTP resta appesa per minuti.

Punti di iniezione (per i test offline e per l'uso programmatico):
- ``db``: istanza :class:`~gmvibe.db.Database` condivisa;
- ``runner``: motore (default: ``StockfishRunner`` creato dal pipeline per job);
- ``coach_client``: client LLM (default: ``AnthropicClient`` costruito da coach).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config as cfg
from .. import paths
from ..ai_coach.coach import coach_analysis
from ..analysis.pipeline import analyze_game
from ..analysis.store import persist_analysis
from ..db import Database
from ..fairplay import FairPlayViolation, gate
from ..models import GameAnalysis, LevelsConfig, PlayerProfile
from ..profile.puzzles import box_due, next_box
from ..profile.weaknesses import build_weakness_profile

__all__ = ["create_app", "AnalyzeRequest", "player_color"]

DEFAULT_USER = "default"


class AnalyzeRequest(BaseModel):
    """Corpo di ``POST /api/analyze``."""

    pgn: str
    profile: str | None = None
    depth: int | None = None
    deep: bool = True


class ReviewRequest(BaseModel):
    """Corpo di ``POST /api/puzzles/{id}/review`` (§7.1)."""

    correct: bool


class SeekRequest(BaseModel):
    """Corpo di ``POST /api/live/seek`` (§8.2)."""

    minutes: float | None = None
    increment: int | None = None
    rated: bool | None = None
    profile: str | None = None


class MoveRequest(BaseModel):
    """Corpo di ``POST /api/live/move`` (§8.2)."""

    uci: str


class DrawRequest(BaseModel):
    """Corpo di ``POST /api/live/draw`` (§8.2)."""

    accept: bool = True


def _puzzle_payload(row: dict) -> dict:
    """Riga di ``puzzles`` pronta per la UI: ``alternatives`` come lista, non JSON."""
    out = dict(row)
    out["alternatives"] = json.loads(row.get("alternatives_json") or "[]")
    return out


def player_color(profile: PlayerProfile | None, analysis: GameAnalysis) -> str | None:
    """Lato del giocatore dedotto dal nickname, o None (entrambi i lati)."""
    if profile is None:
        return None
    nick = profile.nickname.strip().lower()
    if analysis.white and analysis.white.strip().lower() == nick:
        return "white"
    if analysis.black and analysis.black.strip().lower() == nick:
        return "black"
    return None


def _load_profile_safe(nickname: str | None) -> PlayerProfile | None:
    if not nickname:
        return None
    try:
        return cfg.load_profile(nickname)
    except cfg.ConfigError:
        return None


def _require_idle(action: str) -> None:
    """Rifiuta con 409 ogni analisi mentre una partita live e' in corso (§8.3).

    Doppia difesa: qui si respinge subito la richiesta con un messaggio chiaro; i funnel
    (:func:`analyze_game`, :func:`coach_analysis`) ripetono il controllo quando il job
    parte davvero, cosi' non esiste una finestra di gara.
    """
    try:
        gate().require_idle(action=action)
    except FairPlayViolation as exc:
        raise HTTPException(409, str(exc)) from exc


def create_app(
    *,
    db: Database | None = None,
    runner: Any | None = None,
    coach_client: Any | None = None,
    levels: LevelsConfig | None = None,
    live_api: Any | None = None,
) -> FastAPI:
    """Costruisce l'app FastAPI (factory per uvicorn e per i test).

    ``live_api``: client Board API iniettato (nei test un finto; in produzione None, così
    il token del profilo viene letto al momento della ricerca, §4.4).
    """
    from .jobs import JobRegistry

    app = FastAPI(title="GM-Vibe", description="Review di una partita analizzata (§9)")

    try:
        app.state.levels = levels or cfg.load_levels()
    except cfg.ConfigError:
        app.state.levels = levels
    app.state.db = db or Database()
    app.state.runner = runner
    app.state.coach_client = coach_client
    app.state.jobs = JobRegistry()
    app.state.live_api = live_api          # iniettato nei test; None in produzione
    app.state.live = None                  # LiveGameSession attiva (una alla volta)

    # ------------------------------------------------------------------ #
    # Pagina + statici
    # ------------------------------------------------------------------ #
    static_dir = paths.web_static_dir()

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        page = static_dir / "index.html"
        if not page.exists():  # pragma: no cover - asset mancanti
            raise HTTPException(500, "index.html non trovato")
        return FileResponse(page)

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # ------------------------------------------------------------------ #
    # API
    # ------------------------------------------------------------------ #
    @app.get("/api/profiles")
    def profiles(request: Request) -> dict[str, Any]:
        out = []
        for nick in cfg.list_profiles():
            p = _load_profile_safe(nick)
            if p is None:
                out.append({"nickname": nick})
            else:
                out.append({"nickname": p.nickname, "rating": p.rating,
                            "rating_tipo": p.rating_tipo,
                            "cadenza_preferita": p.cadenza_preferita})
        return {"profiles": out}

    @app.post("/api/analyze", status_code=202)
    def analyze(req: AnalyzeRequest, request: Request) -> dict[str, str]:
        pgn = (req.pgn or "").strip()
        if not pgn:
            raise HTTPException(400, "PGN vuoto")
        _require_idle("analisi della partita")  # §8.3
        db_: Database = request.app.state.db
        runner_ = request.app.state.runner
        user_id = req.profile or DEFAULT_USER

        def work() -> dict[str, Any]:
            levels_ = request.app.state.levels or cfg.load_levels()
            analysis, _stats = analyze_game(
                pgn, profile_name=req.profile, depth=req.depth, deep=req.deep,
                db=db_, runner=runner_,
            )
            if not analysis.rows:
                # Un testo non-PGN viene parsato come partita vuota: è un errore di input.
                raise ValueError("il PGN non contiene mosse valide")
            # Persistenza completa (§7): analisi + PGN grezzo + errori etichettati
            # (con ricorrenza) + puzzle. Un unico punto, condiviso con la CLI.
            result = persist_analysis(
                db_, user_id, pgn_text=pgn, analysis=analysis, levels=levels_,
                source="pgn", nickname=req.profile,
            )
            return {"game_id": result.analysis_id, "reused": result.reused}

        job = request.app.state.jobs.submit("analyze", work)
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, request: Request) -> dict[str, Any]:
        job = request.app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job non trovato")
        return job.as_dict()

    @app.get("/api/games")
    def list_games(request: Request, user_id: str | None = None) -> dict[str, Any]:
        return {"games": request.app.state.db.list_analyses(user_id)}

    @app.get("/api/games/{game_id}")
    def get_game(game_id: int, request: Request) -> JSONResponse:
        raw = request.app.state.db.analysis_json(game_id)
        if raw is None:
            raise HTTPException(404, "analisi non trovata")
        return JSONResponse(json.loads(raw))

    # ------------------------------------------------------------------ #
    # Coach (§6) — job separato: la chiamata LLM è lenta
    # ------------------------------------------------------------------ #
    @app.post("/api/games/{game_id}/coach", status_code=202)
    def start_coach(game_id: int, request: Request) -> dict[str, str]:
        _require_idle("analisi col Maestro")  # §8.3
        db_: Database = request.app.state.db
        row = db_.get_analysis(game_id)
        if row is None:
            raise HTTPException(404, "analisi non trovata")
        levels_: LevelsConfig | None = request.app.state.levels
        client = request.app.state.coach_client
        user_id = row["user_id"]

        def work() -> dict[str, Any]:
            analysis = GameAnalysis.model_validate(json.loads(row["analysis_json"]))
            profile = _load_profile_safe(user_id)
            # Un eventuale CoachError (chiave assente, API/rete) diventa lo stato
            # ``error`` del job: la UI lo mostra e resta il report del motore.
            result = coach_analysis(
                analysis, profile=profile,
                config=levels_.coach if levels_ is not None else None,
                client=client,
            )
            db_.save_coach(
                game_id,
                json.dumps(result.report.model_dump(), ensure_ascii=False),
                json.dumps({**result.meta,
                            "usage": result.usage.model_dump() if result.usage else None},
                           ensure_ascii=False),
            )
            if result.usage is not None:
                db_.save_llm_usage(user_id, result.usage, game_id=None)
            return {"game_id": game_id}

        job = request.app.state.jobs.submit("coach", work)
        return {"job_id": job.id}

    @app.get("/api/games/{game_id}/coach")
    def get_coach(game_id: int, request: Request) -> dict[str, Any]:
        row = request.app.state.db.get_analysis(game_id)
        if row is None:
            raise HTTPException(404, "analisi non trovata")
        if not row.get("coach_json"):
            raise HTTPException(404, "report del coach non ancora generato")
        return {
            "report": json.loads(row["coach_json"]),
            "meta": json.loads(row["coach_meta_json"] or "{}"),
        }

    # ------------------------------------------------------------------ #
    # Cross-partita (§7): profilo di debolezze + puzzle con ripasso (§7.1)
    # ------------------------------------------------------------------ #
    @app.get("/api/profile")
    def profile(
        request: Request, user_id: str | None = None, last: int | None = None
    ) -> dict[str, Any]:
        uid = user_id or DEFAULT_USER
        levels_ = request.app.state.levels or cfg.load_levels()
        prof = build_weakness_profile(
            request.app.state.db, uid, window=last, levels=levels_, nickname=uid
        )
        return prof.model_dump()

    @app.get("/api/puzzles")
    def puzzles(
        request: Request, user_id: str | None = None, due: bool = False
    ) -> dict[str, Any]:
        from datetime import datetime, timezone

        uid = user_id or DEFAULT_USER
        due_before = datetime.now(timezone.utc).isoformat() if due else None
        rows = request.app.state.db.list_puzzles(uid, due_before=due_before)
        return {"puzzles": [_puzzle_payload(r) for r in rows]}

    @app.post("/api/puzzles/{puzzle_id}/review")
    def review_puzzle(puzzle_id: int, req: ReviewRequest, request: Request) -> dict[str, Any]:
        from datetime import datetime, timezone

        db_: Database = request.app.state.db
        puzzle = db_.get_puzzle(puzzle_id)
        if puzzle is None:
            raise HTTPException(404, "puzzle non trovato")
        levels_ = request.app.state.levels or cfg.load_levels()
        intervals = levels_.puzzles.leitner_intervals_days
        now = datetime.now(timezone.utc)
        box = next_box(int(puzzle.get("box") or 1), req.correct, intervals)
        due_at = box_due(box, intervals, now)
        db_.apply_review(puzzle_id, box=box, due_at=due_at.isoformat(), correct=req.correct)
        return {"puzzle_id": puzzle_id, "box": box, "due_at": due_at.isoformat(),
                "correct": req.correct}

    # ------------------------------------------------------------------ #
    # Gioco live (§8.2) + guardia fair play (§8.3)
    # ------------------------------------------------------------------ #
    def _build_live_api(profile: str | None) -> Any:
        """Client Board API: quello iniettato (test) o costruito dal token del profilo."""
        if app.state.live_api is not None:
            return app.state.live_api
        from ..lichess_live import BoardAPI, LichessLiveError

        token = None
        if profile:
            try:
                secrets = cfg.load_secrets(profile)
                if secrets.lichess_token is not None:
                    token = secrets.lichess_token.get_secret_value()
            except Exception:
                token = None
        try:
            return BoardAPI(token)
        except LichessLiveError as exc:
            raise HTTPException(400, str(exc)) from exc

    def _current_session() -> Any | None:
        return app.state.live

    @app.get("/api/live/state")
    def live_state(request: Request) -> dict[str, Any]:
        state = request.app.state.live.state if request.app.state.live else None
        return {
            "gate": gate().as_dict(),
            "state": state.model_dump() if state is not None else None,
        }

    @app.post("/api/live/seek", status_code=202)
    def live_seek(req: SeekRequest, request: Request) -> dict[str, Any]:
        if not (request.app.state.levels or cfg.load_levels()).live.enabled:
            raise HTTPException(409, "gioco live disattivato in levels.yaml")
        if gate().active:
            raise HTTPException(409, "una partita live e' gia' in corso")
        # Una sessione gia' in ricerca non alza ancora il gate: senza questo controllo due
        # "Cerca partita" creavano due sessioni concorrenti (§8.2).
        existing = request.app.state.live
        if existing is not None and existing.state.status not in ("idle", "finished"):
            raise HTTPException(409, "una ricerca/partita live e' gia' in corso")
        from ..lichess_live import LiveGameSession

        api = _build_live_api(req.profile)
        me = None
        try:
            me = api.account().get("username")
        except Exception:  # noqa: BLE001 - l'account e' solo per dedurre il colore
            me = None
        live_cfg = (request.app.state.levels or cfg.load_levels()).live
        session = LiveGameSession(
            api, me=me, levels=live_cfg,
            on_update=None,
        )
        session.seek(
            minutes=req.minutes if req.minutes is not None else live_cfg.default_minutes,
            increment=req.increment if req.increment is not None else live_cfg.default_increment,
            rated=bool(req.rated) if req.rated is not None else live_cfg.default_rated,
        )
        request.app.state.live = session
        session.start()
        return {"state": session.state.model_dump(), "gate": gate().as_dict()}

    def _live_session_or_404(request: Request) -> Any:
        session = _current_session()
        if session is None:
            raise HTTPException(404, "nessuna sessione live attiva")
        return session

    @app.post("/api/live/move")
    def live_move(req: MoveRequest, request: Request) -> dict[str, Any]:
        session = _live_session_or_404(request)
        try:
            session.play_move(req.uci)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return session.state.model_dump()

    @app.post("/api/live/resign")
    def live_resign(request: Request) -> dict[str, Any]:
        _live_session_or_404(request).resign()
        return gate().as_dict()

    @app.post("/api/live/abort")
    def live_abort(request: Request) -> dict[str, Any]:
        _live_session_or_404(request).abort()
        return gate().as_dict()

    @app.post("/api/live/draw")
    def live_draw(req: DrawRequest, request: Request) -> dict[str, Any]:
        _live_session_or_404(request).offer_draw(accept=req.accept)
        return gate().as_dict()

    return app
