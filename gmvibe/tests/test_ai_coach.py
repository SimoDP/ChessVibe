"""Test del layer LLM (documentazione.txt §6, FASE 5).

Girano **offline**: il client Anthropic è sostituito da un finto client (duck-typed)
che restituisce testi predisposti. La legalità delle varianti è verificata da
python-chess, come in produzione, quindi i test misurano davvero la proprietà
"100% delle varianti citate sono legali" (§6.4).

Nessuna chiamata di rete, nessuna chiave API richiesta.
"""

from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest

from gmvibe.analysis.pipeline import analyze_game
from gmvibe.ai_coach.anthropic_client import LLMResponse
from gmvibe.ai_coach.coach import CoachError, coach_analysis, engine_only_report
from gmvibe.ai_coach.context import (
    build_context,
    render_system_prompt,
    render_user_prompt,
)
from gmvibe.ai_coach.moments import select_critical_moments
from gmvibe.ai_coach.prompts import load_prompt
from gmvibe.ai_coach.schemas import json_schema_for, parse_report
from gmvibe.ai_coach.usage import add_usage, estimate_cost, usage_from_response
from gmvibe.ai_coach.validators import (
    check_variation_legality,
    sanitize_llm_response,
    validate_report,
)
from gmvibe.models import (
    CoachReport,
    ComplexityBreakdown,
    CitedVariation,
    CoachMoment,
    GameAnalysis,
    LLMUsage,
    MoveRow,
)

FIXTURES = Path(__file__).parent / "fixtures"
START = chess.STARTING_FEN
# FEN con le donne già scambiate (nessuna donna): per il test del cambio donne.
NO_QUEENS = "rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNB1KBNR w KQkq - 0 1"


# --------------------------------------------------------------------------- #
# Client finto: nessuna rete, risposte scriptate + contabilità token finta.
# --------------------------------------------------------------------------- #
class ScriptedClient:
    """Restituisce i testi dati in sequenza (l'ultimo si ripete). Conta le chiamate."""

    def __init__(self, texts: list[str], *, cost: float = 0.01):
        self._texts = list(texts)
        self.calls = 0
        self.last_user_content: str | None = None
        self.last_system: str | None = None
        self._cost = cost

    def complete_structured(self, *, system, messages, schema, model, max_tokens, cache):
        self.calls += 1
        self.last_system = system
        self.last_user_content = messages[0]["content"]
        idx = min(self.calls - 1, len(self._texts) - 1)
        usage = LLMUsage(
            model=model, input_tokens=1200, output_tokens=350,
            cache_read_input_tokens=800, cost_usd=self._cost,
        )
        return LLMResponse(text=self._texts[idx], model=model, usage=usage)


def _report_payload(ctx: dict, *, illegal_first: bool = False, unknown_id: bool = False) -> dict:
    """Costruisce un report conforme al validatore, con varianti legali dai momenti."""
    momenti = []
    for m in ctx["momenti_critici"]:
        board = chess.Board(m["fen_prima"])
        uci = (m.get("mossa_migliore_motore") or {}).get("uci") or m["mossa_giocata_uci"]
        played = board.san(chess.Move.from_uci(uci))
        if illegal_first:
            # "a1a1" non è una mossa legale: deve far fallire la validazione.
            mosse = ["Zz9"]
        else:
            mosse = [played]
        momenti.append({
            "id": ("m999" if unknown_id else m["id"]),
            "fen_prima": m["fen_prima"],
            "mossa_giocata": m["mossa_giocata"],
            "mossa_migliore": played,
            "titolo": "Titolo breve",
            "tipo_errore": m.get("etichetta", ""),
            "spiegazione": "Spiegazione",
            "idea_del_motore": "Idea",
            "regola_di_pensiero": "Regola",
            "varianti_citate": [
                {"fen_partenza": m["fen_prima"], "mosse_san": mosse, "commento": "c"}
            ],
        })
    return {
        "sintesi_partita": "Sintesi della partita.",
        "apertura": {"valutazione": "va", "deviazione": "", "da_ripassare": ""},
        "momenti": momenti,
        "gestione_tempo": "gt",
        "fase_piu_debole": "mediogioco",
        "lezione_finale": "lf",
        "da_allenare": ["a", "b", "c"],
    }


def _grounded_client(**kw) -> ScriptedClient:
    """Client che genera, al momento della chiamata, un report ancorato al contesto."""

    class Grounded(ScriptedClient):
        def __init__(self):
            super().__init__([""])
            self._kw = kw

        def complete_structured(self, *, system, messages, schema, model, max_tokens, cache):
            self.calls += 1
            self.last_user_content = messages[0]["content"]
            self.last_system = system
            ctx = _extract_context(messages[0]["content"])
            text = json.dumps(_report_payload(ctx, **self._kw))
            return LLMResponse(
                text=text, model=model,
                usage=LLMUsage(model=model, input_tokens=1200, output_tokens=350,
                               cache_read_input_tokens=800, cost_usd=0.02),
            )

    return Grounded()


def _extract_context(user_content: str) -> dict:
    """Estrae il pacchetto JSON dal prompt utente (template reale o JSON nudo)."""
    marker = "PACCHETTO DATI (JSON):"
    payload = user_content.split(marker, 1)[1] if marker in user_content else user_content
    return json.loads(payload)


# --------------------------------------------------------------------------- #
# Fixture: analisi sintetica deterministica (senza motore) per i momenti.
# --------------------------------------------------------------------------- #
def _row(ply: int, color: str, *, label="ok", loss=0.0, cx=0.0,
         wb=50.0, wa=50.0, uci="e2e4", best=None, phase="mediogioco",
         fen_before=START, fen_after=START, error_label=None) -> MoveRow:
    return MoveRow(
        ply=ply, move_number=(ply + 1) // 2, color=color, san=f"M{ply}", uci=uci,
        fen_before=fen_before, fen_after=fen_after, label=label,
        winpct_before=wb, winpct_after=wa, winpct_loss=loss,
        complexity=ComplexityBreakdown(score=cx), best_move_uci=best,
        phase=phase, error_label=error_label,
    )


@pytest.fixture
def synthetic_analysis() -> GameAnalysis:
    """Una partita finta con un esempio di ogni categoria di momento (§5.I)."""
    rows = [
        # ply1: transizione di fase apertura -> mediogioco
        _row(1, "white", phase="apertura", wb=50, wa=50),
        _row(2, "black", phase="mediogioco", wb=50, wa=50, best="e7e5", uci="e7e5"),
        # ply3: svarione in posizione complessa -> errore_indotto
        _row(3, "white", label="svarione", loss=25.0, cx=80.0, wb=60, wa=30,
             error_label="tattica_mancata"),
        # ply4: errore che attraversa la soglia 80 (vinto -> meno) -> punto_di_svolta
        _row(4, "black", label="errore", loss=18.0, cx=10.0, wb=88, wa=62),
        # ply5: mossa migliore trovata in posizione complessa -> mossa_eccellente
        _row(5, "white", label="ok", cx=70.0, wb=62, wa=62, uci="d1h5", best="d1h5"),
        # ply6: errore minore, nessun verdetto attraversato -> occasione_mancata
        _row(6, "black", label="errore", loss=8.0, cx=10.0, wb=55, wa=50),
    ]
    return GameAnalysis(rows=rows, white="W", black="B", result="1-0")


@pytest.fixture
def real_analysis(tmp_db, fake_runner, sample_short_text) -> GameAnalysis:
    """Analisi reale via pipeline ma con motore finto (offline)."""
    analysis, _ = analyze_game(sample_short_text, db=tmp_db, runner=fake_runner, depth=8)
    return analysis


# --------------------------------------------------------------------------- #
# §5.I — selezione dei momenti critici
# --------------------------------------------------------------------------- #
def test_moments_cover_categories(synthetic_analysis):
    moments = select_critical_moments(synthetic_analysis, max_moments=7)
    cats = {m.category for m in moments}
    assert "errore_indotto" in cats
    assert "punto_di_svolta" in cats
    assert "mossa_eccellente" in cats
    assert "occasione_mancata" in cats
    assert "transizione_strategica" in cats


def test_moments_respect_max_and_unique_ids(synthetic_analysis):
    moments = select_critical_moments(synthetic_analysis, max_moments=3)
    assert len(moments) <= 3
    ids = [m.id for m in moments]
    assert len(ids) == len(set(ids))


def test_moments_filtered_by_player_color(synthetic_analysis):
    moments = select_critical_moments(synthetic_analysis, player_color="black")
    assert moments, "il nero deve avere almeno un momento"
    assert all(m.color == "black" for m in moments)


def test_moments_chronological_order(synthetic_analysis):
    moments = select_critical_moments(synthetic_analysis)
    plies = [m.ply for m in moments]
    assert plies == sorted(plies)


def test_queens_traded_is_detected(synthetic_analysis):
    """Un cambio donne produce una transizione strategica."""
    rows = [
        _row(1, "white", fen_before=START, fen_after=START),
        _row(2, "black", fen_before=START, fen_after=NO_QUEENS,
             phase="mediogioco", uci="d8d1"),
    ]
    analysis = GameAnalysis(rows=rows)
    moments = select_critical_moments(analysis)
    assert any(m.category == "transizione_strategica" for m in moments)
    assert any("donne" in " ".join(m.reasons) for m in moments)


# --------------------------------------------------------------------------- #
# §6.1 — contesto: mai il PGN intero
# --------------------------------------------------------------------------- #
def test_context_contains_no_pgn(real_analysis):
    moments = select_critical_moments(real_analysis)
    ctx = build_context(real_analysis, moments)
    blob = json.dumps(ctx).lower()
    assert '"pgn"' not in blob and "1.e4" not in blob
    # i momenti presenti sono esattamente quelli selezionati
    assert [m["id"] for m in ctx["momenti_critici"]] == [m.id for m in moments]


def test_context_moment_payload_has_engine_facts(real_analysis):
    moments = select_critical_moments(real_analysis)
    ctx = build_context(real_analysis, moments)
    for payload in ctx["momenti_critici"]:
        assert payload["fen_prima"]
        assert payload["mossa_giocata"]
        assert "linee_motore" in payload
        assert "fen_dopo" in payload


def test_system_prompt_placeholders_are_replaced(real_analysis):
    tpl = load_prompt("system_it")
    out = render_system_prompt(tpl, level_config=real_analysis.level_config, profile=None)
    assert "{" not in out and "}" not in out


def test_user_prompt_injects_context(real_analysis):
    moments = select_critical_moments(real_analysis)
    ctx = build_context(real_analysis, moments)
    out = render_user_prompt(load_prompt("coach_analysis"), ctx)
    assert "{{contesto_json}}" not in out
    assert '"momenti_critici"' in out


# --------------------------------------------------------------------------- #
# §6.3 — schema JSON
# --------------------------------------------------------------------------- #
def test_json_schema_normalized_for_anthropic():
    schema = json_schema_for(CoachReport)
    assert schema["type"] == "object"
    assert schema.get("additionalProperties") is False
    # tutti i campi dichiarati sono in required (structured outputs)
    assert set(schema["required"]) == set(schema["properties"].keys())
    # nessuna parola chiave non supportata sopravvive
    blob = json.dumps(schema)
    for bad in ('"minimum"', '"maxLength"', '"pattern"', '"title"'):
        assert bad not in blob


def test_parse_report_accepts_dict_and_str():
    payload = {"sintesi_partita": "x"}
    assert parse_report(payload).sintesi_partita == "x"
    assert parse_report(json.dumps(payload)).sintesi_partita == "x"


def test_parse_report_rejects_garbage():
    with pytest.raises(Exception):
        parse_report("not json")


# --------------------------------------------------------------------------- #
# §6.4 — validazione: legalità e grounding
# --------------------------------------------------------------------------- #
def test_legal_variation_from_startpos():
    ok, err, uci = check_variation_legality(START, ["e4", "e5", "Nf3"])
    assert ok and err is None
    assert uci == ["e2e4", "e7e5", "g1f3"]


def test_illegal_variation_is_rejected():
    ok, err, _ = check_variation_legality(START, ["e4", "e5", "Nf3", "Nf6", "Qx"])
    assert not ok and err


def test_illegal_move_is_blocking(synthetic_analysis):
    moments = select_critical_moments(synthetic_analysis)
    report = CoachReport(momenti=[
        CoachMoment(
            id=moments[0].id, fen_prima=moments[0].fen_before,
            varianti_citate=[CitedVariation(fen_partenza=moments[0].fen_before,
                                            mosse_san=["Zz9"])],
        )
    ])
    result = validate_report(report, moments)
    assert not result.ok
    assert result.total_variations == 1 and result.legal_variations == 0
    assert any(i.kind == "illegal_move" for i in result.issues)


def test_unknown_fen_is_blocking(synthetic_analysis):
    moments = select_critical_moments(synthetic_analysis)
    report = CoachReport(momenti=[
        CoachMoment(id=moments[0].id, fen_prima=moments[0].fen_before,
                    varianti_citate=[CitedVariation(fen_partenza=NO_QUEENS, mosse_san=["e4"])])
    ])
    result = validate_report(report, moments)
    assert not result.ok
    assert any(i.kind == "unknown_fen" for i in result.issues)


def test_unknown_moment_id_is_blocking(synthetic_analysis):
    moments = select_critical_moments(synthetic_analysis)
    report = CoachReport(momenti=[CoachMoment(id="m999", fen_prima=moments[0].fen_before)])
    result = validate_report(report, moments)
    assert not result.ok
    assert any(i.kind == "unknown_moment" for i in result.issues)


def test_extension_beyond_engine_lines_is_nonblocking(synthetic_analysis):
    """Una variante legale ma non ancorata alle linee fornite è solo da verificare."""
    m = select_critical_moments(synthetic_analysis, max_moments=1)[0]
    # "a2a4" non è tra le mosse fornite del momento (che non ha deep_lines vuote?):
    report = CoachReport(momenti=[
        CoachMoment(id=m.id, fen_prima=m.fen_before,
                    varianti_citate=[CitedVariation(fen_partenza=m.fen_before,
                                                    mosse_san=["h4", "h5"])])
    ])
    result = validate_report(report, [m])
    assert result.ok, "l'estensione non deve bloccare"
    assert any(i.kind == "extension" and not i.blocking for i in result.issues)


def test_sanitize_strips_code_fences_and_tags():
    raw = "```json\n{\"a\": 1}\n```\n<b>x</b>"
    out = sanitize_llm_response(raw)
    assert "```" not in out and "<b>" not in out
    assert '"a": 1' in out


# --------------------------------------------------------------------------- #
# §6 — orchestrazione
# --------------------------------------------------------------------------- #
def test_coach_valid_report_is_validated(real_analysis):
    client = _grounded_client()
    result = coach_analysis(real_analysis, client=client, user_template="{{contesto_json}}")
    assert result.validated
    assert client.calls == 1
    assert result.meta["varianti_legali_pct"] == 100.0
    assert result.meta["varianti_totali"] > 0
    assert real_analysis.coach is not None
    assert real_analysis.coach_usage is not None


def test_coach_regenerates_once_then_validates(real_analysis):
    illegal = json.dumps(_report_payload(_ctx_of(real_analysis), illegal_first=True))
    valid = json.dumps(_report_payload(_ctx_of(real_analysis)))
    client = ScriptedClient([illegal, valid])
    result = coach_analysis(real_analysis, client=client, user_template="{{contesto_json}}")
    assert client.calls == 2
    assert result.meta["regenerated"] is True
    assert result.validated
    assert result.meta["varianti_legali_pct"] == 100.0


def test_coach_falls_back_after_failed_regeneration(real_analysis):
    illegal = json.dumps(_report_payload(_ctx_of(real_analysis), illegal_first=True))
    client = ScriptedClient([illegal, illegal])
    result = coach_analysis(real_analysis, client=client, user_template="{{contesto_json}}")
    assert client.calls == 2  # una sola rigenerazione (§6.4)
    assert not result.validated
    assert result.meta["fallback"] is True
    # fallback: nessuna variante citata -> banalmente legale, ma con avviso
    assert all(not mo.varianti_citate for mo in result.report.momenti)
    assert "non ha superato la validazione" in result.report.sintesi_partita


def test_coach_unknown_moment_id_triggers_regeneration(real_analysis):
    bad = json.dumps(_report_payload(_ctx_of(real_analysis), unknown_id=True))
    good = json.dumps(_report_payload(_ctx_of(real_analysis)))
    client = ScriptedClient([bad, good])
    result = coach_analysis(real_analysis, client=client, user_template="{{contesto_json}}")
    assert client.calls == 2
    assert result.validated


def test_coach_no_moments_uses_engine_only():
    analysis = GameAnalysis(rows=[], white="W", black="B")
    result = coach_analysis(analysis)
    assert result.meta["fallback"] is True
    assert result.meta["reason"] == "nessun momento critico"
    assert result.report.momenti == []


def test_coach_client_error_raises_coach_error(real_analysis):
    class Boom:
        def complete_structured(self, **kwargs):
            raise CoachError("chiave assente")

    with pytest.raises(CoachError):
        coach_analysis(real_analysis, client=Boom(), user_template="{{contesto_json}}")


def test_engine_only_report_has_no_variations(synthetic_analysis):
    moments = select_critical_moments(synthetic_analysis)
    report = engine_only_report(synthetic_analysis, moments)
    assert len(report.momenti) == len(moments)
    assert all(m.varianti_citate == [] for m in report.momenti)


def _ctx_of(analysis: GameAnalysis) -> dict:
    moments = select_critical_moments(analysis)
    return build_context(analysis, moments)


# --------------------------------------------------------------------------- #
# §6.5 — contabilità token e costo
# --------------------------------------------------------------------------- #
def test_estimate_cost_matches_prices():
    cost = estimate_cost("claude-opus-4-8", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(30.0)  # 5 + 25 usd per 1M


def test_estimate_cost_accounts_cache():
    base = estimate_cost("claude-opus-4-8", input_tokens=1000, output_tokens=0)
    cached = estimate_cost("claude-opus-4-8", input_tokens=0, output_tokens=0,
                           cache_read_input_tokens=1000)
    assert cached == pytest.approx(base * 0.10, rel=0.01)


def test_add_usage_sums_and_merges_model():
    a = LLMUsage(model="m1", input_tokens=10, output_tokens=5, cost_usd=0.001)
    b = LLMUsage(model="m1", input_tokens=20, output_tokens=7, cost_usd=0.002)
    total = add_usage(a, b)
    assert total.input_tokens == 30 and total.output_tokens == 12
    assert total.model == "m1"
    assert add_usage(None, b) is b


def test_usage_from_response_reads_cache_fields():
    class U:
        input_tokens = 100
        output_tokens = 50
        cache_read_input_tokens = 40
        cache_creation_input_tokens = 10

    u = usage_from_response("claude-haiku-4-5", U())
    assert u.cache_read_input_tokens == 40
    assert u.cache_creation_input_tokens == 10
    assert u.cost_usd > 0


# --------------------------------------------------------------------------- #
# Prompt su disco
# --------------------------------------------------------------------------- #
def test_prompts_loadable():
    for name in ("system_it", "coach_analysis"):
        text = load_prompt(name)
        assert text.strip()


# --------------------------------------------------------------------------- #
# §10 FASE 5 — criterio di accettazione: 10 partite, 100% varianti legali
# --------------------------------------------------------------------------- #
def test_acceptance_ten_games_all_variations_legal(tmp_db, fake_runner):
    """Su 10 partite, il 100% delle varianti citate è legale e nessun momento è
    mostrato senza validazione (§6.4). Usa i prompt reali su disco."""
    texts = [
        (FIXTURES / n).read_text(encoding="utf-8")
        for n in ("sample_short.pgn", "sample_draw.pgn", "sample_noclk.pgn", "sample_endgame.pgn")
    ]
    games = [texts[i % len(texts)] for i in range(10)]

    total_variations = 0
    total_cost = 0.0
    for text in games:
        analysis, _ = analyze_game(text, db=tmp_db, runner=fake_runner, depth=8)
        client = _grounded_client()
        result = coach_analysis(analysis, client=client)
        assert result.validated, "ogni partita deve superare la validazione"
        assert result.meta["varianti_legali_pct"] == 100.0
        total_variations += result.meta["varianti_totali"]
        total_cost += result.usage.cost_usd
        # ogni momento mostrato è passato dalla validazione
        assert all(not m.varianti_citate or result.validation.ok for m in result.report.momenti)

    assert total_variations > 0, "il campione deve contenere varianti citate"
    assert total_cost > 0, "il costo in token deve essere tracciato per partita"


def test_acceptance_negative_control_illegal_is_caught(tmp_db, fake_runner):
    """Controllo negativo: una mossa illegale NON deve passare la validazione."""
    analysis, _ = analyze_game(
        (FIXTURES / "sample_short.pgn").read_text(encoding="utf-8"),
        db=tmp_db, runner=fake_runner, depth=8,
    )
    illegal = json.dumps(_report_payload(_ctx_of(analysis), illegal_first=True))
    result = coach_analysis(analysis, client=ScriptedClient([illegal]), user_template="{{contesto_json}}")
    assert not result.validated
    assert result.meta["validated"] is False
    assert result.meta["fallback"] is True


# --------------------------------------------------------------------------- #
# Regressioni di validazione (§6.4) e robustezza del client
# --------------------------------------------------------------------------- #
def _one_moment(real_analysis):
    moments = select_critical_moments(real_analysis)
    assert moments, "la partita di esempio deve avere almeno un momento"
    return moments


def test_validate_rejects_report_without_moments(real_analysis):
    """Un report vuoto (schema valido ma senza momenti) NON deve passare come valido:
    scarterebbe in silenzio i momenti scelti dal motore."""
    moments = _one_moment(real_analysis)
    report = parse_report(json.dumps({"momenti": []}))
    result = validate_report(report, moments)
    assert result.ok is False
    assert any(i.kind == "missing_moments" for i in result.issues)


def test_validate_rejects_mismatched_id_and_fen(real_analysis):
    """id e FEN di un momento devono appartenere allo STESSO momento."""
    moments = _one_moment(real_analysis)
    a = moments[0]
    if len(moments) < 2:
        pytest.skip("servono almeno due momenti")
    b = moments[1]
    report = CoachReport(momenti=[CoachMoment(
        id=a.id, fen_prima=b.fen_before, mossa_giocata=a.san,
        mossa_migliore=a.best_move_san or "", titolo="t",
    )])
    result = validate_report(report, moments)
    assert result.ok is False
    assert any(i.kind == "mismatched_fen" for i in result.issues)


def test_coach_wraps_unexpected_client_exception(real_analysis):
    """Un client iniettato che solleva un'eccezione NON-AnthropicError va comunque
    incapsulata in CoachError (CLI/web catturano solo CoachError)."""

    class Boom:
        def complete_structured(self, **kw):
            raise TimeoutError("rete lenta")

    with pytest.raises(CoachError):
        coach_analysis(real_analysis, client=Boom())


def test_coach_empty_report_falls_back(real_analysis):
    """Risposta vuota -> fallback ai dati del motore, mai mostrata come 'validata'."""
    client = ScriptedClient(['{"momenti": []}'])
    result = coach_analysis(real_analysis, client=client)
    assert result.validated is False
    assert result.meta.get("fallback") is True


# --------------------------------------------------------------------------- #
# §6.5 — provider OpenRouter: stesso flusso validato, indipendente dal fornitore
# --------------------------------------------------------------------------- #
def test_coach_works_with_openrouter_provider(real_analysis):
    """Con provider=openrouter e un client iniettato (finto), il report è validato come
    per Anthropic: la legalità delle varianti è garantita dal validatore, non dal provider."""
    from gmvibe.models import CoachConfig

    cfg = CoachConfig()
    cfg.provider = "openrouter"
    client = _grounded_client()
    result = coach_analysis(real_analysis, client=client, config=cfg,
                            user_template="{{contesto_json}}")
    assert result.validated
    assert result.meta["varianti_legali_pct"] == 100.0


def test_coach_uses_client_model_not_anthropic_default(real_analysis):
    """Il modello richiesto è quello del client (es. slug OpenRouter), non il default
    Anthropic della config: altrimenti il gateway non troverebbe il modello."""
    captured: list[str] = []

    class CaptureClient:
        model = "gateway/vendor-model"

        def complete_structured(self, *, system, messages, schema, model, max_tokens, cache):
            captured.append(model)
            text = json.dumps(_report_payload(_ctx_of(real_analysis)))
            return LLMResponse(text=text, model=model,
                               usage=LLMUsage(model=model, input_tokens=1, output_tokens=1))

    result = coach_analysis(real_analysis, client=CaptureClient(),
                            user_template="{{contesto_json}}")
    assert captured == ["gateway/vendor-model"]
    assert result.meta["model"] == "gateway/vendor-model"


def test_coach_openrouter_without_key_raises_coach_error(real_analysis, monkeypatch):
    """Senza client iniettato, con provider=openrouter e nessuna chiave, la factory costruisce
    il cliente OpenRouter che fallisce in modo pulito: CoachError, mai eccezioni grezze."""
    from gmvibe.models import CoachConfig

    from gmvibe.models import Secrets

    monkeypatch.setattr("gmvibe.ai_coach.openrouter_client.load_secrets",
                        lambda profile: Secrets())
    cfg = CoachConfig()
    cfg.provider = "openrouter"
    with pytest.raises(CoachError) as exc:
        coach_analysis(real_analysis, config=cfg, user_template="{{contesto_json}}")
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_ai_cache_key_includes_system_prompt(tmp_path):
    """Due richieste con lo stesso messaggio ma system diverso non devono collidere."""
    from gmvibe.ai_coach.cache import AICache

    cache = AICache(tmp_path / "c.db")
    msgs = [{"role": "user", "content": "ciao"}]
    cache.put("A", messages=msgs, model="m", max_tokens=10, temperature=0.0, system="sys-A")
    assert cache.get(messages=msgs, model="m", max_tokens=10, temperature=0.0,
                     system="sys-A") == "A"
    assert cache.get(messages=msgs, model="m", max_tokens=10, temperature=0.0,
                     system="sys-B") is None
