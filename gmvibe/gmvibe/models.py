"""Modelli Pydantic v2 del progetto (documentazione.txt §3, §4.1, §4.3, §5).

Regola: nessun dato personale o numero di Elo hardcoded qui; i campi esistono, i valori
vengono dal profilo e da ``levels.yaml``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr

RatingTipo = Literal["fide", "lichess", "chesscom", "altro"]
Cadenza = Literal["blitz", "rapid", "classica"]
Color = Literal["white", "black"]
Phase = Literal["apertura", "mediogioco", "finale"]
MoveLabel = Literal["ok", "imprecisione", "errore", "svarione"]
# Natura dell'errore (documentazione.txt §5.G)
ErrorLabel = Literal[
    "tattica_mancata",
    "pezzo_in_presa",
    "valutazione_errata",
    "piano_sbagliato",
    "profilassi_mancata",
    "sicurezza_re",
    "tecnica_conversione",
    "tecnica_difesa",
    "finale_tecnica",
    "tempo",
    "teoria",
]


# --------------------------------------------------------------------------- #
# Profilo utente (§4.1)
# --------------------------------------------------------------------------- #
class Account(BaseModel):
    lichess: str | None = None
    chesscom: str | None = None


class Repertorio(BaseModel):
    bianco: list[str] = Field(default_factory=list)
    nero_vs_e4: list[str] = Field(default_factory=list)
    nero_vs_d4: list[str] = Field(default_factory=list)


class PlayerProfile(BaseModel):
    """``profiles/<nickname>/profile.yaml`` — non contiene MAI segreti (§4.1)."""

    nickname: str
    lingua_report: str = "it"
    rating: int = Field(ge=0, le=4000)
    rating_tipo: RatingTipo = "fide"
    cadenza_preferita: Cadenza = "blitz"
    account: Account = Field(default_factory=Account)
    repertorio: Repertorio = Field(default_factory=Repertorio)
    debolezze_dichiarate: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Configurazione dei livelli (§4.2, levels.yaml)
# --------------------------------------------------------------------------- #
class ErrorThresholds(BaseModel):
    """Soglie di classificazione in punti di win% (§5.E)."""

    imprecisione: float
    errore: float
    svarione: float


class LevelAnchor(BaseModel):
    rating: int
    error_thresholds: ErrorThresholds
    critical_moments: int
    natural_move_prob: float
    theory_depth: int
    descrittore_livello: str
    maia_bucket: int | None = None


class FastPassConfig(BaseModel):
    multipv: int = 1
    depth: int | None = 14
    time: float = 0.3
    use_wdl: bool = True


class DeepPassConfig(BaseModel):
    """Pass profondo (§5.D): solo sui candidati, MultiPV=4, profondita' 22-26 o 5-10s.

    ``max_candidates`` limita quanti candidati si approfondiscono (0 = nessun limite):
    una partita caotica puo' avere decine di candidati e, a depth 22-26, ognuno costa
    decine di secondi. Si approfondiscono prima le mosse col calo di win% maggiore, cosi'
    il tempo resta prevedibile (obiettivo dichiarato del design a due passate).
    """

    multipv: int = 4
    depth: int | None = None   # se impostato ha precedenza su `time`; 24 = ~150s/posizione
    time: float = 6.0          # secondi per posizione (spec §5.D: 5-10s)
    low_depth: int = 10   # eval a bassa profondita' per misurare la "trappolosita'"
    max_candidates: int = 16   # 0 = nessun limite; tetto al costo del pass profondo


class CandidateSelectionConfig(BaseModel):
    """Selezione dei candidati al pass profondo (§5.C).

    Nota: ``winpct_drop`` e' tarato SOPRA il rumore del pass veloce (a depth ~14 una
    mossa buona oscilla di ~2-3 punti di win%): una soglia piu' bassa renderebbe
    candidata quasi ogni mossa e il pass profondo non sarebbe piu' limitato ai momenti
    critici. ``verdict_levels`` sono i CONFINI tra i verdetti (vinto/patta/perso), non il
    centro della fascia di patta: 80 (vinto<->patta) e 20 (patta<->perso). Una mossa e'
    candidata se ATTRAVERSA un confine, non se semplicemente vi si avvicina.
    """

    winpct_drop: float = 5.0          # calo minimo di win% per essere candidato
    verdict_levels: list[float] = Field(default_factory=lambda: [80.0, 20.0])
    clock_high_seconds: float = 60.0  # tempo speso molto
    clock_low_seconds: float = 1.0    # tempo speso pochissimo (mossa "istantanea")


class EngineConfig(BaseModel):
    """Risorse del motore Stockfish (§3). Nessun valore hardcoded nel codice (CLAUDE.md).

    ``threads == 0`` significa "usa tutti i core disponibili": il pass profondo a
    profondita' 22-26 su una singola thread puo' richiedere minuti per posizione.
    """

    threads: int = 0       # 0 = tutti i core disponibili
    hash_mb: int = 256     # 0 = lascia il default del motore


class ComplexityWeights(BaseModel):
    gap: float = 2.0
    reasonable_moves: float = 2.0
    instability: float = 1.0
    counterintuitive: float = 1.0
    # Fattore 5 (Maia, §5.H, opzionale FASE 8): peso della "poco naturalezza" umana.
    maia: float = 1.0


class ComplexityConfig(BaseModel):
    """Complexity Score composto (§5.H), fattori 1-4 (+5 Maia se disponibile)."""

    reasonable_margin_winpct: float = 5.0   # mosse entro questo margine dalla migliore
    weights: ComplexityWeights = Field(default_factory=ComplexityWeights)


class ClassificationConfig(BaseModel):
    """Gestione delle posizioni gia' decise (§5.E)."""

    decided_winpct_high: float = 95.0   # sopra: ancora vinto non si segnala
    decided_keep_winning: float = 90.0  # se resta sopra, l'errore e' irrilevante
    decided_winpct_low: float = 5.0     # sotto: gia' perso, non si segnala


class TablebaseConfig(BaseModel):
    """Finali con tablebase (documentazione.txt §5.K)."""

    enabled: bool = True
    syzygy_path: str | None = None     # cartella Syzygy locale (opzionale; <= 5 pezzi)
    syzygy_max_pieces: int = 5
    lichess_max_pieces: int = 7        # fino a 7 pezzi via API Lichess
    allow_network: bool = True


class OpeningExplorerConfig(BaseModel):
    """Opening Explorer di Lichess (documentazione.txt §5.L)."""

    enabled: bool = True
    use_masters: bool = True
    use_lichess: bool = True
    min_games: int = 5                 # sotto: la posizione non ha teoria affidabile
    allow_network: bool = True


class TimeProfile(BaseModel):
    """Soglie di tempo per una cadenza (documentazione.txt §5.J)."""

    zeitnot_seconds: float | None = 15.0   # None = disattivato (partite OTB lunghe)
    banal_time_seconds: float = 60.0       # tempo speso "molto" su una mossa banale
    critical_fast_seconds: float = 5.0     # mossa critica giocata troppo in fretta


class TimeAnalysisConfig(BaseModel):
    enabled: bool = True
    profiles: dict[str, TimeProfile] = Field(default_factory=dict)


class ErrorTaxonomyConfig(BaseModel):
    """Soglie della classificazione della *natura* dell'errore (§5.G)."""

    zeitnot_seconds: float = 15.0          # sotto: errore "da tempo"
    banal_time_seconds: float = 120.0      # sopra, su mossa banale -> errore "da tempo"
    banal_winpct_margin: float = 5.0       # mossa considerata banale se il calo atteso e' minimo
    king_safety_delta: int = 2              # variazione scudo di pedoni del Re = sicurezza_re
    conversion_winpct: float = 80.0         # vinto sopra -> tecnica_conversione se crolla
    defense_winpct_band: float = 20.0       # sotto 50 ma sopra questa fascia -> tenibile
    forcing_plies: int = 4                  # semimosse forzanti per "tattica_mancata"


class MaiaConfig(BaseModel):
    """Maia via Lc0 (§4.2, §5.H, FASE 8). OPZIONALE: se Lc0/weights mancano si disattiva.

    L'invocazione esatta di Lc0 (percorso dei pesi, rating Maia-2, opzioni) vive in
    ``lc0_args``/``weights_dir`` — mai nel codice (CLAUDE.md). Il numero di nodi e il
    tempo di ricerca sono qui per riproducibilita'.
    """

    enabled: bool = True
    maia1_buckets: list[int] = Field(default_factory=list)
    # Percorso del binario Lc0 (fallback: env ``GMVIBE_LC0``, poi PATH).
    lc0_path: str | None = None
    # Cartella dei pesi Maia (nomi file composti da ``weights_pattern`` + bucket/rating).
    weights_dir: str | None = None
    weights_pattern: str = "maia-{bucket}.pb.gz"
    # Argomenti extra passati a Lc0, es. ["--verbose-move-stats"] per leggere la policy.
    lc0_args: list[str] = Field(default_factory=lambda: ["--verbose-move-stats"])
    nodes: int = 1          # la policy di Maia si legge a bassa profondita' (1 nodo)
    time: float | None = None
    # Fattore 5: sotto questa probabilita' umana la posizione conta come "difficile".
    hard_move_prob: float = 0.10


class OpenRouterConfig(BaseModel):
    """Gateway OpenRouter (§6.5): qualunque modello tramite un'unica chiave.

    Lo slug del modello e' libero (``anthropic/...``, ``openai/...``, ``deepseek/...``):
    i default sono slug Anthropic ma restano sovrascrivibili in ``levels.yaml`` o da
    ambiente (``GMVIBE_OPENROUTER_MODEL`` / ``GMVIBE_OPENROUTER_MODEL_FAST``). Gli slug
    disponibili si leggono da ``GET https://openrouter.ai/api/v1/models``.
    """

    base_url: str = "https://openrouter.ai/api/v1"
    model_main: str = "anthropic/claude-opus-4.8"
    model_fast: str = "anthropic/claude-haiku-4.5"
    # Header facoltativi di identificazione app (OpenRouter li consiglia).
    site_url: str = ""
    app_name: str = "GM-Vibe"
    timeout_seconds: float = 120.0


class CoachConfig(BaseModel):
    """Layer LLM (§6). Modelli e limiti vivono qui, mai nel codice (CLAUDE.md).

    I modelli si possono sovrascrivere da ``.env`` (``GMVIBE_COACH_MODEL`` /
    ``GMVIBE_COACH_MODEL_FAST``): vedi ``ai_coach.anthropic_client``. Il provider
    (``anthropic`` diretto o ``openrouter``) si sceglie da ``provider`` o da
    ``GMVIBE_COACH_PROVIDER``: vedi ``ai_coach.client_factory``.
    """

    enabled: bool = True
    # Provider del layer LLM: SDK Anthropic diretto oppure gateway OpenRouter.
    provider: Literal["anthropic", "openrouter"] = "anthropic"
    # §6.5: modello migliore per i 5-7 momenti, modello veloce per classificazioni
    # leggere e sintesi finale.
    model_main: str = "claude-opus-4-8"
    model_fast: str = "claude-haiku-4-5"
    max_moments: int = 7               # §5.I: al massimo 5-7 momenti per partita
    min_moments: int = 1
    max_tokens_main: int = 8192
    max_tokens_fast: int = 4096
    regenerate_on_invalid: int = 1     # §6.4: rigenera una volta in caso di errore
    use_structured_output: bool = True  # output_config.format (json_schema)
    cache_enabled: bool = True          # prompt caching sui blocchi stabili
    timeout_seconds: float = 120.0
    # Parametri del gateway OpenRouter (usati solo se provider == "openrouter").
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)


class PuzzleConfig(BaseModel):
    """Generatore di puzzle e ripasso dilazionato (§7.1). Soglie in config, mai nel codice."""

    enabled: bool = True
    min_winpct_loss: float = 8.0        # sotto: l'errore non merita un puzzle
    max_per_game: int = 5
    # Intervalli Leitner per box 1..N (giorni). L'ultimo box = consolidato.
    leitner_intervals_days: list[int] = Field(default_factory=lambda: [1, 3, 7, 16, 35])


class ProfileConfig(BaseModel):
    """Profilo di debolezze cross-partita (§7)."""

    window_games: int = 20              # finestra "ultime N partite"
    clock_buckets_seconds: list[float] = Field(default_factory=lambda: [10.0, 30.0, 120.0])
    min_games_opening: int = 1          # sotto: l'apertura non entra nel repertorio


class LiveConfig(BaseModel):
    """Parametri del gioco live (§8.2) e della modalita' cooldown (§8.4)."""

    enabled: bool = True
    default_minutes: float = 5.0        # cadenza di ricerca di default (minuti)
    default_increment: int = 3          # incremento (secondi)
    default_rated: bool = False         # casual di default (il rated richiede board:play)
    # §8.4: dopo ogni partita "Nuova partita" resta bloccato finche' non si legge il
    # report. Opzionale, attivabile da config.
    cooldown_enabled: bool = False


class LevelsConfig(BaseModel):
    version: int = 1
    notes: str = ""
    winpct_thresholds_default: ErrorThresholds
    fast_pass: FastPassConfig = Field(default_factory=FastPassConfig)
    deep_pass: DeepPassConfig = Field(default_factory=DeepPassConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    candidate_selection: CandidateSelectionConfig = Field(default_factory=CandidateSelectionConfig)
    complexity: ComplexityConfig = Field(default_factory=ComplexityConfig)
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    error_taxonomy: ErrorTaxonomyConfig = Field(default_factory=ErrorTaxonomyConfig)
    tablebase: TablebaseConfig = Field(default_factory=TablebaseConfig)
    opening_explorer: OpeningExplorerConfig = Field(default_factory=OpeningExplorerConfig)
    time_analysis: TimeAnalysisConfig = Field(default_factory=TimeAnalysisConfig)
    anchors: list[LevelAnchor]
    maia: MaiaConfig = Field(default_factory=MaiaConfig)
    coach: CoachConfig = Field(default_factory=CoachConfig)
    puzzles: PuzzleConfig = Field(default_factory=PuzzleConfig)
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)

    def sorted_anchors(self) -> list[LevelAnchor]:
        return sorted(self.anchors, key=lambda a: a.rating)


# --------------------------------------------------------------------------- #
# Conversione rating (§4.3.1, rating_conversion.yaml)
# --------------------------------------------------------------------------- #
class CadenzaDelta(BaseModel):
    delta: int


class ClampRange(BaseModel):
    min: int = 400
    max: int = 3500


class RatingConversion(BaseModel):
    version: int = 1
    notes: str = ""
    default_cadenza: Cadenza = "rapid"
    tables: dict[str, dict[str, CadenzaDelta]]
    clamp: ClampRange = Field(default_factory=ClampRange)


# --------------------------------------------------------------------------- #
# LevelConfig risolto (§4.3) — salvato con ogni analisi, per riproducibilita'
# --------------------------------------------------------------------------- #
class LevelConfig(BaseModel):
    """Risultato di ``resolve_level``: tutti i parametri dipendenti dal livello."""

    rating_input: int
    rating_tipo: RatingTipo
    cadenza: Cadenza
    rating_lichess: int
    rating_lichess_source: Literal["declared_lichess", "converted"]
    estimated: bool
    extrapolated: bool
    error_thresholds: ErrorThresholds
    critical_moments: int
    natural_move_prob: float
    theory_depth: int
    descrittore_livello: str
    maia_model: str | None = None
    maia_kind: Literal["maia2", "maia1", "none"] = "none"
    calibration_line: str = ""


# --------------------------------------------------------------------------- #
# Segreti (§4.4) — mai persistiti/loggati
# --------------------------------------------------------------------------- #
class Secrets(BaseModel):
    anthropic_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    lichess_token: SecretStr | None = None


# --------------------------------------------------------------------------- #
# Valutazione del motore (§5.B, §5.D)
# --------------------------------------------------------------------------- #
class WDL(BaseModel):
    """Conteggi win/draw/loss per mille, dal punto di vista del lato indicato."""

    wins: int
    draws: int
    losses: int


class PVLine(BaseModel):
    rank: int
    move_uci: str
    move_san: str | None = None
    score_cp: int | None = None
    mate: int | None = None
    wdl: WDL | None = None
    pv_uci: list[str] = Field(default_factory=list)


class EngineEval(BaseModel):
    fen: str
    depth: int
    multipv: int
    engine_name: str
    engine_version: str
    turn: Color
    lines: list[PVLine] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Analisi della partita (§5, §9.1)
# --------------------------------------------------------------------------- #
class ComplexityBreakdown(BaseModel):
    """Componenti del Complexity Score (§5.H). Fattori 1-4 (+ Maia opzionale, FASE 8)."""

    gap_winpct: float = 0.0
    reasonable_moves: int = 0
    instability: float = 0.0
    counterintuitive: float = 0.0
    # Fattore 5 (Maia): probabilita' che un umano del livello giochi la mossa migliore.
    # ``None`` quando Maia non e' disponibile/disattivato -> il fattore non entra.
    maia_human_prob: float | None = None
    score: float = 0.0
    note: str = ""


class SideFeatures(BaseModel):
    """Fatti posizionali di un lato, calcolati con python-chess (documentazione.txt §5.F)."""

    has_bishop_pair: bool = False
    isolated_pawns: list[str] = Field(default_factory=list)
    doubled_pawns: list[str] = Field(default_factory=list)
    backward_pawns: list[str] = Field(default_factory=list)
    passed_pawns: list[str] = Field(default_factory=list)
    iqp: bool = False                 # pedone di donna isolato
    hanging_pawns: bool = False       # pedoni sospesi (c3+d4 / c6+d5)
    outposts: list[str] = Field(default_factory=list)
    bad_bishop: bool = False
    rooks_on_seventh: int = 0
    mobility: int = 0
    king_pawn_shield: int = 0
    castled: bool = False


class PositionFeatures(BaseModel):
    """Fact sheet strutturato per entrambi i lati (§5.F). Claude riceve questi fatti."""

    material_balance: int = 0          # bianco - nero (valori semplici)
    opposite_colored_bishops: bool = False
    open_files: list[str] = Field(default_factory=list)
    semi_open_files: list[str] = Field(default_factory=list)
    center_control: int = 0            # bianco - nero sul centro allargato
    white: SideFeatures = Field(default_factory=SideFeatures)
    black: SideFeatures = Field(default_factory=SideFeatures)


class MoveRow(BaseModel):
    ply: int                      # 1-based semimossa
    move_number: int
    color: Color
    san: str
    uci: str
    fen_before: str
    fen_after: str
    score_cp: int | None = None
    mate: int | None = None
    winpct_before: float | None = None
    winpct_after: float | None = None
    winpct_loss: float | None = None
    clock_seconds: float | None = None
    clock_delta: float | None = None
    opening_name: str | None = None
    opening_eco: str | None = None
    in_book: bool = False
    in_cache: bool = False
    # --- FASE 2 ---
    phase: Phase | None = None
    label: MoveLabel = "ok"
    is_candidate: bool = False
    deep_depth: int | None = None
    best_move_uci: str | None = None
    best_move_san: str | None = None
    gap_winpct: float | None = None
    complexity: ComplexityBreakdown | None = None
    deep_lines: list[PVLine] = Field(default_factory=list)
    # --- FASE 3 ---
    features_before: PositionFeatures | None = None
    features_delta: dict[str, float] = Field(default_factory=dict)
    error_label: ErrorLabel | None = None
    # --- FASE 7 ---
    recurrence_games: int = 0     # partite precedenti con lo stesso error_label (§7)


class TablebaseVerdict(BaseModel):
    """Verdetto esatto di un finale (§5.K). Mai dedotto da Claude."""

    fen: str
    pieces: int
    category: Literal["win", "draw", "loss", "cursed_win", "blessed_loss", "unknown"]
    dtz: int | None = None
    dtm: int | None = None
    best_move_uci: str | None = None
    source: Literal["syzygy", "lichess"]


class EndgameVerdict(BaseModel):
    """Verdetto sui finali: dove il risultato e' cambiato (§5.K)."""

    ply: int
    san: str
    fen_before: str
    fen_after: str
    verdict_before: TablebaseVerdict | None = None
    verdict_after: TablebaseVerdict | None = None
    result_changed: bool = False


class OpeningInfo(BaseModel):
    """Nome/ECO dell'apertura e punto di uscita dalla teoria (§5.A, §5.L)."""

    eco: str | None = None
    name: str | None = None
    exit_ply: int | None = None
    exit_move_san: str | None = None
    exit_move_uci: str | None = None
    explorer_stats: dict | None = None


class TimeMoveNote(BaseModel):
    ply: int
    san: str
    color: Color
    kind: str            # "banale_lenta" | "critica_veloce" | "zeitnot"
    seconds: float | None = None


class TimeSummary(BaseModel):
    """Analisi del tempo (documentazione.txt §5.J)."""

    cadenza: str = "blitz"
    white_avg: float | None = None
    black_avg: float | None = None
    white_max: float | None = None
    black_max: float | None = None
    zeitnot_moves: int = 0
    notes: list[TimeMoveNote] = Field(default_factory=list)


class AccuracySummary(BaseModel):
    bianco: float | None = None
    nero: float | None = None


# --------------------------------------------------------------------------- #
# Momenti critici (§5.I) — oggetto passato a Claude, mai il PGN intero (§6.1)
# --------------------------------------------------------------------------- #
class CriticalMoment(BaseModel):
    """Un momento critico isolato per l'analisi LLM (§5.I).

    Autonomo: contiene FEN prima/dopo, mossa giocata (SAN+UCI), top-4 linee con eval
    e WDL, fact sheet posizionale, delta feature, etichetta d'errore, Complexity Score
    e i tempi sul clock — tutto ciò che serve al Maestro virtuale, e nient'altro.
    """

    id: str                          # es. "m22"
    ply: int
    color: Color
    san: str
    uci: str
    fen_before: str
    fen_after: str
    category: str                    # punto_di_svolta | errore_indotto | mossa_eccellente | ...
    impact: float = 0.0              # rilevanza per l'ordinamento (decrescente)
    label: MoveLabel = "ok"
    error_label: ErrorLabel | None = None
    best_move_san: str | None = None
    best_move_uci: str | None = None
    winpct_before: float | None = None
    winpct_after: float | None = None
    winpct_loss: float | None = None
    score_cp: int | None = None
    mate: int | None = None
    clock_seconds: float | None = None
    clock_delta: float | None = None
    phase: Phase | None = None
    complexity: ComplexityBreakdown | None = None
    features_before: PositionFeatures | None = None
    features_delta: dict[str, float] = Field(default_factory=dict)
    deep_lines: list[PVLine] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    recurrence_games: int = 0     # partite precedenti con lo stesso error_label (§7)


# --------------------------------------------------------------------------- #
# Layer LLM: output strutturato (§6.3) e contabilità token/costo (§6.5)
# --------------------------------------------------------------------------- #
class CitedVariation(BaseModel):
    """Una variante citata dall'LLM: va validata da ``fen_partenza`` (§6.4)."""

    fen_partenza: str
    mosse_san: list[str] = Field(default_factory=list)
    commento: str = ""


class CoachMoment(BaseModel):
    """Un momento nel report del coach (§6.3)."""

    id: str
    fen_prima: str = ""
    mossa_giocata: str = ""
    mossa_migliore: str = ""
    titolo: str = ""
    tipo_errore: str = ""
    spiegazione: str = ""
    idea_del_motore: str = ""
    regola_di_pensiero: str = ""
    varianti_citate: list[CitedVariation] = Field(default_factory=list)


class OpeningComment(BaseModel):
    valutazione: str = ""
    deviazione: str = ""
    da_ripassare: str = ""


class CoachReport(BaseModel):
    """Risposta JSON del Maestro virtuale (§6.3). Mai Markdown libero."""

    sintesi_partita: str = ""
    apertura: OpeningComment = Field(default_factory=OpeningComment)
    momenti: list[CoachMoment] = Field(default_factory=list)
    gestione_tempo: str = ""
    fase_piu_debole: str = ""
    lezione_finale: str = ""
    da_allenare: list[str] = Field(default_factory=list)


class LLMUsage(BaseModel):
    """Contabilità token di una chiamata LLM (§6.5). Il costo è una stima."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0


class GameAnalysis(BaseModel):
    headers: dict[str, str] = Field(default_factory=dict)
    result: str = "*"
    white: str | None = None
    black: str | None = None
    level_config: LevelConfig | None = None
    rows: list[MoveRow] = Field(default_factory=list)
    # --- FASE 2 ---
    accuracy: AccuracySummary = Field(default_factory=AccuracySummary)
    accuracy_by_phase: dict[str, AccuracySummary] = Field(default_factory=dict)
    counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    # --- FASE 4 ---
    opening: OpeningInfo | None = None
    endgame: list[EndgameVerdict] = Field(default_factory=list)
    time: TimeSummary | None = None
    # --- FASE 5 ---
    moments: list[CriticalMoment] = Field(default_factory=list)
    coach: CoachReport | None = None
    coach_usage: LLMUsage | None = None
    coach_meta: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Analisi cross-partita (§7): profilo di debolezze e puzzle con ripasso (§7.1)
# --------------------------------------------------------------------------- #
class LabelFrequency(BaseModel):
    """Etichetta d'errore e quante partite/volte ricorre nella finestra (§7)."""

    error_label: str
    games: int
    occurrences: int
    phase: str | None = None            # fase più tipica per questa etichetta
    clock_bucket: str | None = None     # fascia di clock più tipica


class PhaseAccuracy(BaseModel):
    fase: str
    bianco: float | None = None
    nero: float | None = None
    media: float | None = None


class CadenzaAccuracy(BaseModel):
    cadenza: str
    games: int
    bianco: float | None = None
    nero: float | None = None


class OpeningStat(BaseModel):
    """Statistica di repertorio per apertura: "quali linee ti fanno perdere" (§7)."""

    opening: str
    eco: str | None = None
    games: int
    wins: int = 0
    draws: int = 0
    losses: int = 0
    accuracy: float | None = None


class ClockBucket(BaseModel):
    """Errori in funzione del tempo sul clock (§7)."""

    label: str
    moves: int
    errors: int
    error_rate: float


class EvolutionPoint(BaseModel):
    """Un punto della curva di evoluzione (una partita, in ordine cronologico)."""

    game_id: int
    date: str
    accuracy: float | None = None
    finale_accuracy: float | None = None


class WeaknessProfile(BaseModel):
    """Profilo di debolezze cross-partita (§7). Aggregato, deterministico."""

    user_id: str
    window: int
    games_analyzed: int = 0
    label_frequency: list[LabelFrequency] = Field(default_factory=list)
    accuracy_by_phase: list[PhaseAccuracy] = Field(default_factory=list)
    accuracy_by_color: AccuracySummary = Field(default_factory=AccuracySummary)
    accuracy_by_cadenza: list[CadenzaAccuracy] = Field(default_factory=list)
    clock_buckets: list[ClockBucket] = Field(default_factory=list)
    repertoire: list[OpeningStat] = Field(default_factory=list)
    evolution: list[EvolutionPoint] = Field(default_factory=list)
    trend_finali: str = "dati_insufficienti"
    da_allenare: list[str] = Field(default_factory=list)


class Puzzle(BaseModel):
    """Puzzle generato da un errore (§7.1).

    La soluzione è la mossa migliore **calcolata dal motore** (mai dall'LLM); la
    spiegazione è deterministica (natura dell'errore + fatti posizionali).
    """

    user_id: str
    fen: str
    solution_uci: str
    solution_san: str | None = None
    alternatives: list[str] = Field(default_factory=list)   # UCI accettati oltre alla soluzione
    theme: str | None = None                                # etichetta §5.G
    explanation: str = ""
    id: int | None = None
    game_id: int | None = None
    ply: int | None = None
    created_at: str | None = None
    # --- stato del ripasso (join con puzzle_reviews) ---
    box: int | None = None
    due_at: str | None = None
    times_seen: int = 0
    times_correct: int = 0


# --------------------------------------------------------------------------- #
# Gioco live su Lichess (§8.2-8.3, FASE 8)
# --------------------------------------------------------------------------- #
class LiveGameState(BaseModel):
    """Stato serializzabile di una partita live (§8.2). Nessun segreto qui dentro."""

    status: Literal["idle", "searching", "playing", "finished"] = "idle"
    game_id: str | None = None
    fen: str = "startpos"
    initial_fen: str | None = None   # FEN di partenza (puo' non essere lo startpos)
    moves: list[str] = Field(default_factory=list)     # UCI, in ordine
    last_move: str | None = None
    my_color: Literal["white", "black"] | None = None
    me: str | None = None
    opponent: str | None = None
    rated: bool = False
    clock_white: float | None = None
    clock_black: float | None = None
    result: str | None = None                          # "1-0" / "0-1" / "1/2-1/2"
    winner: str | None = None                          # "white"/"black"/None (patta)
    game_in_progress: bool = False                     # = gate §8.3
    message: str | None = None


