// GM-Vibe — UI di review (§9). Vanilla JS, nessun framework.
// La scacchiera è Chessground (asset vendorizzati); chess.js serve solo a
// riprodurre e verificare la legalità delle varianti citate, non a ricalcolare eval.

import { Chessground } from '/static/vendor/chessground.min.js';
import { Chess } from '/static/vendor/chess.js';

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

const CATEGORY_LABEL = {
  punto_di_svolta: 'Punto di svolta',
  errore_indotto: 'Errore indotto',
  mossa_eccellente: 'Mossa eccellente',
  occasione_mancata: 'Occasione mancata',
  transizione_strategica: 'Transizione strategica',
  uscita_dalla_teoria: 'Uscita dalla teoria',
  posizione_complessa: 'Posizione complessa',
};
const NATURE_LABEL = {
  tattica_mancata: 'tattica mancata', pezzo_in_presa: 'pezzo in presa',
  valutazione_errata: 'valutazione errata', piano_sbagliato: 'piano sbagliato',
  profilassi_mancata: 'profilassi mancata', sicurezza_re: 'sicurezza del re',
  tecnica_conversione: 'conversione', tecnica_difesa: 'difesa',
  finale_tecnica: 'finale', tempo: 'tempo', teoria: 'teoria',
};

const state = {
  analysis: null,
  positions: [],   // FEN per ogni indice 0..N (0 = posizione iniziale)
  moves: [],       // UCI per ogni indice >=1
  index: 0,
  board: null,
  orientation: 'white',
  profileNick: '',
  activeMoment: null,
};

const $ = (id) => document.getElementById(id);

// Escape per l'inserimento in innerHTML. I dati mostrati (nomi dei giocatori dal PGN,
// testo del Maestro, nomi apertura, messaggi d'errore) NON sono fidati: un PGN importato
// da Lichess/Chess.com o incollato puo' contenere HTML. Senza escape si ha XSS memorizzato.
const _ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => _ESC[c]);
}

// --------------------------------------------------------------------------- //
// API
// --------------------------------------------------------------------------- //
async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

async function pollJob(jobId, onTick) {
  for (;;) {
    const st = await api(`/api/jobs/${jobId}`);
    if (onTick) onTick(st);
    if (st.status === 'done') return st;
    if (st.status === 'error') throw new Error(st.error || 'job fallito');
    await new Promise((res) => setTimeout(res, 400));
  }
}

// --------------------------------------------------------------------------- //
// Scacchiera e navigazione
// --------------------------------------------------------------------------- //
function uciPair(uci) { return uci ? [uci.slice(0, 2), uci.slice(2, 4)] : undefined; }

function initBoard() {
  state.board = Chessground($('board'), {
    fen: START_FEN, orientation: 'white', coordinates: true,
    animation: { enabled: true, duration: 150 },
    movable: { free: false, color: undefined },
    drawable: { enabled: true, visible: true },
  });
}

function setBoard(fen, lastMoveUci, shapes = []) {
  state.board.set({
    fen,
    orientation: state.orientation,
    lastMove: uciPair(lastMoveUci),
    drawable: { shapes },
  });
}

function buildPositions(analysis) {
  const rows = analysis.rows || [];
  state.positions = [];
  state.moves = [];
  if (rows.length === 0) { state.positions = [START_FEN]; return; }
  state.positions.push(rows[0].fen_before);
  for (const row of rows) { state.positions.push(row.fen_after); state.moves.push(row.uci); }
}

function setIndex(i, { focus = false } = {}) {
  const n = state.positions.length - 1;
  state.index = Math.max(0, Math.min(i, n));
  const last = state.index > 0 ? state.moves[state.index - 1] : undefined;
  setBoard(state.positions[state.index], last);
  const rows = state.analysis ? state.analysis.rows : [];
  const lbl = state.index === 0
    ? 'posizione iniziale'
    : `${rows[state.index - 1].move_number}${rows[state.index - 1].color === 'white' ? '.' : '…'} ${rows[state.index - 1].san}`;
  $('plyLabel').textContent = `${lbl}   (${state.index}/${n})`;
  if (focus) highlightMomentForPly(state.index);
}

function highlightMomentForPly(index) {
  const ply = index; // rows[i-1] è la mossa che porta a index
  document.querySelectorAll('.moment').forEach((el) => {
    el.classList.toggle('active', Number(el.dataset.ply) === ply);
  });
}

// --------------------------------------------------------------------------- //
// Grafici
// --------------------------------------------------------------------------- //
const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(name, attrs = {}) {
  const el = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function whiteWin(row) {
  const v = row.winpct_after ?? row.winpct_before ?? 50;
  return row.color === 'white' ? v : 100 - v;
}

function renderEvalChart(analysis) {
  const svg = $('evalChart');
  svg.innerHTML = '';
  const rows = analysis.rows || [];
  const W = 100, H = 100, n = rows.length;
  if (n === 0) return;
  const x = (i) => (n === 1 ? 0 : (i / (n - 1)) * W);
  const y = (p) => H - (p / 100) * H;

  // fascia grigia + linea del 50%
  svg.appendChild(svgEl('rect', { x: 0, y: 0, width: W, height: H, fill: '#1b1e26' }));
  svg.appendChild(svgEl('line', { x1: 0, y1: y(50), x2: W, y2: y(50), stroke: '#3a4150', 'stroke-width': 0.4 }));

  const pts = rows.map((r, i) => `${x(i)},${y(whiteWin(r))}`).join(' ');
  svg.appendChild(svgEl('polyline', { points: pts, fill: 'none', stroke: '#7aa2f7', 'stroke-width': 1 }));

  // momenti critici marcati
  for (const m of analysis.moments || []) {
    const i = rows.findIndex((r) => r.ply === m.ply);
    if (i < 0) continue;
    svg.appendChild(svgEl('circle', { cx: x(i), cy: y(whiteWin(rows[i])), r: 1.6, fill: '#f7768e' }));
  }
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
}

function renderTimeChart(analysis) {
  const svg = $('timeChart');
  svg.innerHTML = '';
  const rows = analysis.rows || [];
  const n = rows.length;
  if (n === 0) return;
  const deltas = rows.map((r) => r.clock_delta ?? 0);
  const max = Math.max(1, ...deltas);
  const W = 100, H = 100, bw = W / n;
  svg.appendChild(svgEl('rect', { x: 0, y: 0, width: W, height: H, fill: '#1b1e26' }));
  rows.forEach((r, i) => {
    const h = (deltas[i] / max) * H;
    svg.appendChild(svgEl('rect', {
      x: i * bw, y: H - h, width: Math.max(0.5, bw * 0.7), height: h,
      fill: r.color === 'white' ? '#c9d1e0' : '#5b6577',
    }));
  });
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
}

// --------------------------------------------------------------------------- //
// Report testuale
// --------------------------------------------------------------------------- //
function fmtPct(v) { return v == null ? '–' : `${v.toFixed(1)}`; }

function renderSummary(a) {
  const acc = a.accuracy || {};
  const parts = [];
  parts.push(`<div class="acc-row">
    <div><div class="big">${fmtPct(acc.bianco)}</div><div class="lbl">Bianco</div></div>
    <div><div class="big">${fmtPct(acc.nero)}</div><div class="lbl">Nero</div></div>
    <div><div class="big">${esc(a.white ?? '?')} – ${esc(a.black ?? '?')}</div><div class="lbl">${esc(a.result ?? '*')}</div></div>
  </div>`);
  const byPhase = a.accuracy_by_phase || {};
  if (Object.keys(byPhase).length) {
    let html = '<table class="phase"><tr><th>Fase</th><th>Bianco</th><th>Nero</th></tr>';
    for (const ph of ['apertura', 'mediogioco', 'finale']) {
      const s = byPhase[ph]; if (!s) continue;
      html += `<tr><td>${ph}</td><td>${fmtPct(s.bianco)}</td><td>${fmtPct(s.nero)}</td></tr>`;
    }
    html += '</table>';
    parts.push(html);
  }
  $('summary').innerHTML = parts.join('');
  $('summary').classList.remove('muted');
}

function renderOpening(a) {
  const op = a.opening;
  if (!op) return;
  const panel = $('openingPanel');
  panel.hidden = false;
  let html = '';
  if (!op.name) {
    html = '<span class="muted">Posizione iniziale non standard (nessuna teoria).</span>';
  } else {
    html += `<h3>${esc(op.name)}${op.eco ? ` <span class="muted">(${esc(op.eco)})</span>` : ''}</h3>`;
    html += op.exit_ply != null
      ? `<div>Uscita dalla teoria alla mossa ${esc(op.exit_ply)} (<b>${esc(op.exit_move_san ?? '')}</b>).</div>`
      : '<div class="muted">Ancora in teoria nel campione analizzato.</div>';
  }
  $('opening').innerHTML = html;
}

function momentShapes(m) {
  const shapes = [];
  if (m.uci) shapes.push({ orig: m.uci.slice(0, 2), dest: m.uci.slice(2, 4), brush: 'red' });
  if (m.best_move_uci) shapes.push({ orig: m.best_move_uci.slice(0, 2), dest: m.best_move_uci.slice(2, 4), brush: 'green' });
  return shapes;
}

function renderMoments(a) {
  const moments = a.moments || [];
  const panel = $('momentsPanel');
  if (!moments.length) { panel.hidden = true; return; }
  panel.hidden = false;
  const host = $('moments');
  host.innerHTML = '';
  for (const m of moments) {
    const div = document.createElement('div');
    div.className = 'moment';
    div.dataset.ply = m.ply;
    const label = m.label && m.label !== 'ok' ? `<span class="pill ${m.label}">${m.label}</span>` : '';
    const nature = m.error_label ? `<span class="pill">${NATURE_LABEL[m.error_label] || m.error_label}</span>` : '';
    // §7: "questo errore è già comparso in X partite precedenti"
    const rec = m.recurrence_games > 0
      ? `<span class="pill badge-rec">già comparso in ${m.recurrence_games} partite</span>` : '';
    const cx = m.complexity ? `cx ${Math.round(m.complexity.score)}` : '';
    const why = [
      m.winpct_loss != null ? `calo ${m.winpct_loss.toFixed(1)} win%` : '',
      m.clock_seconds != null ? `${Math.round(m.clock_seconds)}s sul clock` : '',
      cx, ...(m.reasons || []),
    ].filter(Boolean).join(' · ');
    const num = Math.ceil(m.ply / 2);
    div.innerHTML = `
      <div class="head">
        <div><span class="san">${num}${m.color === 'white' ? '.' : '…'} ${esc(m.san)}</span>
          ${m.best_move_san ? `<span class="muted">→ migliore <span class="best">${esc(m.best_move_san)}</span></span>` : ''}</div>
        <div>${label} ${nature}</div>
      </div>
      ${rec ? `<div>${rec}</div>` : ''}
      <div class="why">${esc(CATEGORY_LABEL[m.category] || m.category)}${why ? ' — ' + esc(why) : ''}</div>`;
    div.onclick = () => focusMoment(m);
    host.appendChild(div);
  }
}

function focusMoment(m) {
  const rows = state.analysis.rows;
  const i = rows.findIndex((r) => r.ply === m.ply);
  if (i < 0) return;
  state.index = i; // posizione *prima* della mossa (rows[i].fen_before)
  setBoard(m.fen_before, undefined, momentShapes(m));
  const row = rows[i];
  $('plyLabel').textContent = `${row.move_number}${row.color === 'white' ? '.' : '…'} ${row.san} — prima della mossa`;
  highlightMomentForPly(m.ply);
}

function renderGood(a) {
  const good = (a.moments || []).filter((m) => m.category === 'mossa_eccellente');
  const panel = $('goodPanel');
  if (!good.length) { panel.hidden = true; return; }
  panel.hidden = false;
  $('good').innerHTML = good.map((m) =>
    `<div class="moment"><b>${esc(m.san)}</b> <span class="muted">(${m.best_move_uci ? 'migliore del motore' : ''}${m.complexity ? `, cx ${Math.round(m.complexity.score)}` : ''})</span>
     <div class="why">${esc((m.reasons || []).join(' · ') || 'mossa forte riconosciuta')}</div></div>`).join('');
}

function renderTime(a) {
  const t = a.time;
  if (!t) return;
  $('timePanel').hidden = false;
  const secs = (v) => (v == null ? '–' : `${Math.round(v)}s`);
  let html = `<div>Cadenza <b>${t.cadenza}</b> — medio B ${secs(t.white_avg)} / N ${secs(t.black_avg)}, max B ${secs(t.white_max)} / N ${secs(t.black_max)}, mosse in zeitnot: ${t.zeitnot_moves}</div>`;
  if ((t.notes || []).length) {
    html += '<ul>' + t.notes.map((n) => `<li>${esc(n.san)} (${esc(n.color)}): ${esc(n.kind)} — ${secs(n.seconds)}</li>`).join('') + '</ul>';
  }
  $('timeInfo').innerHTML = html;
}

function renderEndgame(a) {
  const eg = a.endgame || [];
  if (!eg.length) return;
  $('endgamePanel').hidden = false;
  $('endgame').innerHTML = eg.map((v) => {
    const before = v.verdict_before ? v.verdict_before.category : '?';
    const after = v.verdict_after ? v.verdict_after.category : '?';
    return `<div>mossa ${v.ply} <b>${esc(v.san)}</b>: ${esc(before)} → ${esc(after)}${v.result_changed ? ' <span class="pill">cambia il risultato</span>' : ''}</div>`;
  }).join('');
}

function render(analysis) {
  state.analysis = analysis;
  state.profileNick = $('profile').value;
  // orientamento: se il profilo è il Nero, gira la scacchiera
  state.orientation = (state.profileNick && analysis.black &&
    analysis.black.toLowerCase() === state.profileNick.toLowerCase()) ? 'black' : 'white';

  buildPositions(analysis);
  setIndex(0);
  renderSummary(analysis);
  renderOpening(analysis);
  renderMoments(analysis);
  renderGood(analysis);
  renderTime(analysis);
  renderEndgame(analysis);
  renderEvalChart(analysis);
  renderTimeChart(analysis);
  $('coachPanel').hidden = false;
  $('coach').innerHTML = '<span class="muted">Premi “Analizza col Maestro” per il commento (§6).</span>';
  $('coachStatus').textContent = '';
}

// --------------------------------------------------------------------------- //
// Coach (§6) — rendering del JSON validato
// --------------------------------------------------------------------------- //
function renderCoach(data, analysis) {
  const { report, meta } = data;
  const parts = [];
  const badge = meta.validated
    ? `<span class="pill" style="color:var(--good);border-color:var(--good)">validato</span>`
    : `<span class="pill" style="color:var(--warn);border-color:var(--warn)">soli dati motore</span>`;
  parts.push(`<div>${badge} ${meta.varianti_totali != null ? `<span class="muted">varianti legali ${meta.varianti_legali}/${meta.varianti_totali} (${meta.varianti_legali_pct}%)</span>` : ''}</div>`);
  if (meta.usage) parts.push(`<div class="muted">costo: ${meta.usage.input_tokens} in / ${meta.usage.output_tokens} out token ≈ $${(meta.usage.cost_usd || 0).toFixed(4)} [${meta.usage.model}]</div>`);
  if (report.sintesi_partita) parts.push(`<p class="coach-sintesi">${esc(report.sintesi_partita)}</p>`);

  const op = report.apertura || {};
  if (op.valutazione || op.deviazione || op.da_ripassare) {
    parts.push('<h3>Apertura</h3>');
    if (op.valutazione) parts.push(`<div>${esc(op.valutazione)}</div>`);
    if (op.deviazione) parts.push(`<div class="muted">deviazione: ${esc(op.deviazione)}</div>`);
    if (op.da_ripassare) parts.push(`<div class="muted">da ripassare: ${esc(op.da_ripassare)}</div>`);
  }

  const byId = new Map((analysis.moments || []).map((m) => [m.id, m]));
  if ((report.momenti || []).length) {
    parts.push('<h3>Momenti</h3>');
    for (const cm of report.momenti) {
      const m = byId.get(cm.id);
      const title = cm.titolo || (m ? `${m.san}` : cm.id);
      const variants = (cm.varianti_citate || []).map((v, idx) =>
        `<span class="variant" data-mid="${esc(cm.id)}" data-vidx="${idx}">▷ ${esc(v.mosse_san.join(' '))}</span>`).join('');
      parts.push(`<div class="moment">
        <div class="head"><span class="san">${esc(title)}</span>
          ${m ? `<span class="muted">${esc(m.san)}${m.best_move_san ? ` → ${esc(m.best_move_san)}` : ''}</span>` : ''}</div>
        ${cm.spiegazione ? `<div>${esc(cm.spiegazione)}</div>` : ''}
        ${cm.idea_del_motore ? `<div class="muted">idea del motore: ${esc(cm.idea_del_motore)}</div>` : ''}
        ${cm.regola_di_pensiero ? `<div class="regola"><b>Regola di pensiero:</b> ${esc(cm.regola_di_pensiero)}</div>` : ''}
        ${variants}
      </div>`);
    }
  }
  if (report.gestione_tempo) parts.push(`<h3>Tempo</h3><div>${esc(report.gestione_tempo)}</div>`);
  if (report.fase_piu_debole) parts.push(`<div class="muted">Fase più debole: ${esc(report.fase_piu_debole)}</div>`);
  if (report.lezione_finale) parts.push(`<h3>Lezione finale</h3><div>${esc(report.lezione_finale)}</div>`);
  if ((report.da_allenare || []).length) {
    parts.push('<h3>Da allenare</h3><ul class="train-list">' + report.da_allenare.map((x) => `<li>${esc(x)}</li>`).join('') + '</ul>');
  }
  $('coach').innerHTML = parts.join('');

  // click su una variante → la riproduce sulla scacchiera
  $('coach').querySelectorAll('.variant').forEach((el) => {
    el.onclick = () => {
      const cm = report.momenti.find((x) => x.id === el.dataset.mid);
      const v = cm && cm.varianti_citate[Number(el.dataset.vidx)];
      if (v) playVariation(v);
    };
  });
}

function playVariation(v) {
  const chess = new Chess(v.fen_partenza);
  setBoard(v.fen_partenza, undefined);
  const step = (i) => {
    if (i >= v.mosse_san.length) return;
    const move = chess.move(v.mosse_san[i]);
    if (!move) return;
    setBoard(chess.fen(), move.from + move.to);
    setTimeout(() => step(i + 1), 500);
  };
  step(0);
}

// --------------------------------------------------------------------------- //
// Flusso: analisi e coach
// --------------------------------------------------------------------------- //
async function runAnalyze() {
  const pgn = $('pgn').value.trim();
  if (!pgn) { $('status').textContent = 'Incolla un PGN.'; return; }
  $('analyze').disabled = true;
  $('status').textContent = 'Analisi in corso…';
  try {
    const { job_id } = await api('/api/analyze', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ pgn, profile: $('profile').value || null }),
    });
    const st = await pollJob(job_id, (s) => { $('status').textContent = `Analisi ${s.status}… (${s.elapsed}s)`; });
    const analysis = await api(`/api/games/${st.game_id}`);
    analysis.__id = st.game_id;   // serve al pulsante del coach
    render(analysis);
    $('status').textContent = st.reused
      ? 'Analisi aggiornata (partita già presente, nessun duplicato).'
      : 'Analisi completata.';
  } catch (e) {
    $('status').innerHTML = `<span style="color:var(--bad)">Errore: ${esc(e.message)}</span>`;
  } finally {
    $('analyze').disabled = false;
  }
}

async function runCoach() {
  if (!state.analysis) return;
  const gid = state.analysis.__id || (state.analysis.__id = await currentGameId());
  if (!gid) return;
  $('runCoach').disabled = true;
  $('coachStatus').textContent = 'Il Maestro sta analizzando…';
  try {
    const { job_id } = await api(`/api/games/${gid}/coach`, { method: 'POST' });
    await pollJob(job_id, (s) => { $('coachStatus').textContent = `Maestro ${s.status}… (${s.elapsed}s)`; });
    const data = await api(`/api/games/${gid}/coach`);
    renderCoach(data, state.analysis);
    $('coachStatus').textContent = '';
  } catch (e) {
    $('coachStatus').innerHTML = `<span style="color:var(--bad)">Non disponibile: ${esc(e.message)}</span>`;
  } finally {
    $('runCoach').disabled = false;
  }
}

async function currentGameId() {
  // il game_id più recente dell'elenco (l'analisi appena salvata)
  try { const { games } = await api('/api/games'); return games[0] && games[0].id; } catch { return null; }
}

async function loadProfiles() {
  try {
    const { profiles } = await api('/api/profiles');
    const sel = $('profile');
    for (const p of profiles) {
      const opt = document.createElement('option');
      opt.value = p.nickname;
      opt.textContent = p.rating ? `${p.nickname} (${p.rating})` : p.nickname;
      sel.appendChild(opt);
    }
    if (profiles.length === 1) sel.value = profiles[0].nickname;
  } catch { /* nessun profilo: si analizza senza calibrazione */ }
}

function wire() {
  $('analyze').onclick = runAnalyze;
  $('runCoach').onclick = runCoach;
  $('first').onclick = () => setIndex(0, { focus: true });
  $('prev').onclick = () => setIndex(state.index - 1, { focus: true });
  $('next').onclick = () => setIndex(state.index + 1, { focus: true });
  $('last').onclick = () => setIndex(state.positions.length - 1, { focus: true });
  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'ArrowLeft') { setIndex(state.index - 1, { focus: true }); e.preventDefault(); }
    if (e.key === 'ArrowRight') { setIndex(state.index + 1, { focus: true }); e.preventDefault(); }
  });
  $('loadExample').onclick = () => {
    $('pgn').value = `[Event "Esempio"]
[White "BiancoTest"]
[Black "NeroTest"]
[Result "1-0"]
[TimeControl "300+0"]

1. e4 { [%clk 0:05:00] } 1... e5 { [%clk 0:04:59] } 2. Nf3 { [%clk 0:04:57] }
2... Nc6 { [%clk 0:04:55] } 3. Bc4 { [%clk 0:04:53] } 3... d6 { [%clk 0:04:50] }
4. Nc3 { [%clk 0:04:48] } 4... Bg4 { [%clk 0:04:45] } 5. Nxe5 { [%clk 0:04:41] }
5... Bxd1 { [%clk 0:04:30] } 6. Bxf7+ { [%clk 0:04:25] } 6... Ke7 { [%clk 0:04:10] }
7. Nd5# { [%clk 0:04:05] } 1-0`;
    $('status').textContent = 'PGN di esempio caricato. Premi “Analizza”.';
  };
  $('tabReview').onclick = () => switchTab('review');
  $('tabProfile').onclick = () => switchTab('profile');
  $('loadProfile').onclick = loadWeaknessProfile;
  $('reloadPuzzles').onclick = loadPuzzles;
  $('startReview').onclick = startReview;
}

// --------------------------------------------------------------------------- //
// FASE 7 — Profilo di debolezze (§7) e puzzle con ripasso (§7.1)
// --------------------------------------------------------------------------- //
function switchTab(name) {
  $('reviewView').hidden = name !== 'review';
  $('profileView').hidden = name !== 'profile';
  $('liveView').hidden = name !== 'live';
  $('tabReview').classList.toggle('active', name === 'review');
  $('tabProfile').classList.toggle('active', name === 'profile');
  $('tabLive').classList.toggle('active', name === 'live');
  if (name === 'profile') { loadWeaknessProfile(); loadPuzzles(); }
  if (name === 'live') { initLiveBoard(); refreshLive(); }
}

function pct(v) { return v == null ? '–' : v.toFixed(1); }

function renderWeakProfile(p) {
  const host = $('weakProfile');
  host.classList.remove('muted');
  if (!p.games_analyzed) {
    host.innerHTML = '<span class="muted">Nessuna analisi per questo profilo. ' +
      'Usa <code>python -m gmvibe import --lichess …</code> (o analizza con un profilo selezionato).</span>';
    return;
  }
  const parts = [`<div class="muted">Ultime ${p.window} partite — ${p.games_analyzed} analizzate.</div>`];

  if ((p.label_frequency || []).length) {
    parts.push('<h3>Etichette più frequenti</h3><table class="stats"><tr><th>Pattern</th><th>Partite</th><th>Occorrenze</th><th></th></tr>');
    for (const lf of p.label_frequency.slice(0, 8)) {
      const w = Math.round((lf.games / Math.max(1, p.games_analyzed)) * 100);
      const extra = [lf.phase, lf.clock_bucket ? `clock ${lf.clock_bucket}` : ''].filter(Boolean).join(' · ');
      parts.push(`<tr><td>${NATURE_LABEL[lf.error_label] || lf.error_label}${extra ? `<div class="muted">${extra}</div>` : ''}</td>
        <td>${lf.games} su ${p.games_analyzed}</td><td>${lf.occurrences}</td>
        <td><span class="bar bad" style="width:${w}%"></span></td></tr>`);
    }
    parts.push('</table>');
  }

  if ((p.accuracy_by_phase || []).length) {
    parts.push('<h3>Accuratezza per fase</h3><table class="stats"><tr><th>Fase</th><th>Bianco</th><th>Nero</th><th>Media</th></tr>');
    for (const ph of p.accuracy_by_phase)
      parts.push(`<tr><td>${ph.fase}</td><td>${pct(ph.bianco)}</td><td>${pct(ph.nero)}</td><td>${pct(ph.media)}</td></tr>`);
    parts.push('</table>');
    parts.push(`<div class="muted">Per colore: bianco ${pct(p.accuracy_by_color.bianco)} / nero ${pct(p.accuracy_by_color.nero)}</div>`);
  }

  if ((p.accuracy_by_cadenza || []).length) {
    parts.push('<h3>Per cadenza</h3><table class="stats"><tr><th>Cadenza</th><th>Partite</th><th>Bianco</th><th>Nero</th></tr>');
    for (const c of p.accuracy_by_cadenza)
      parts.push(`<tr><td>${c.cadenza}</td><td>${c.games}</td><td>${pct(c.bianco)}</td><td>${pct(c.nero)}</td></tr>`);
    parts.push('</table>');
  }

  if ((p.clock_buckets || []).length) {
    parts.push('<h3>Errori in funzione del clock</h3><table class="stats"><tr><th>Fascia</th><th>Errori</th><th>Mosse</th><th>Tasso</th></tr>');
    for (const b of p.clock_buckets)
      parts.push(`<tr><td>${b.label}</td><td>${b.errors}</td><td>${b.moves}</td><td>${(b.error_rate * 100).toFixed(0)}%</td></tr>`);
    parts.push('</table>');
  }

  if ((p.repertoire || []).length) {
    parts.push('<h3>Repertorio</h3><table class="stats"><tr><th>Apertura</th><th>Partite</th><th>+ / = / −</th><th>Acc.</th></tr>');
    for (const o of p.repertoire.slice(0, 8))
      parts.push(`<tr><td>${o.opening}${o.eco ? ` <span class="muted">${o.eco}</span>` : ''}</td>
        <td>${o.games}</td><td>${o.wins} / ${o.draws} / ${o.losses}</td><td>${pct(o.accuracy)}</td></tr>`);
    parts.push('</table>');
  }

  if ((p.evolution || []).length > 1) {
    const withAcc = p.evolution.filter((e) => e.accuracy != null);
    const first = withAcc[0], last = withAcc[withAcc.length - 1];
    parts.push(`<h3>Evoluzione</h3><div>${p.evolution.length} partite in ordine: accuratezza ${pct(first && first.accuracy)} → ${pct(last && last.accuracy)}.
      Tendenza dei finali: <b>${p.trend_finali}</b>.</div>`);
  }

  if ((p.da_allenare || []).length) {
    parts.push('<h3>Da allenare</h3><ul class="train-list">' +
      p.da_allenare.map((x) => `<li>${x}</li>`).join('') + '</ul>');
  }
  host.innerHTML = parts.join('');
}

async function loadWeaknessProfile() {
  const nick = $('profile').value;
  const last = $('window').value || 20;
  $('weakProfile').classList.add('muted');
  $('weakProfile').textContent = 'Caricamento…';
  try {
    const q = new URLSearchParams({ last: String(last) });
    if (nick) q.set('user_id', nick);
    const p = await api(`/api/profile?${q.toString()}`);
    renderWeakProfile(p);
  } catch (e) {
    $('weakProfile').innerHTML = `<span style="color:var(--bad)">Errore: ${esc(e.message)}</span>`;
  }
}

// --- Puzzle e ripasso (SRS Leitner) ---
const puzzleState = { board: null, queue: [], idx: 0, current: null, answered: false };

function initPuzzleBoard() {
  puzzleState.board = Chessground($('puzzleBoard'), {
    fen: START_FEN, orientation: 'white', coordinates: true,
    animation: { enabled: true, duration: 130 },
    movable: { free: true, color: 'white', events: { after: onPuzzleMove } },
    drawable: { enabled: true, visible: true },
  });
}

function onPuzzleMove(orig, dest) {
  const p = puzzleState.current;
  if (!p) return;
  if (puzzleState.answered) { puzzleState.board.set({ fen: p.fen }); return; }
  const ch = new Chess(p.fen);
  let mv = null;
  try { mv = ch.move({ from: orig, to: dest, promotion: 'q' }); } catch { mv = null; }
  if (!mv) { puzzleState.board.set({ fen: p.fen }); return; }  // mossa illegale: annulla
  gradePuzzle(mv.from + mv.to + (mv.promotion || ''));
}

function showPuzzle() {
  const p = puzzleState.queue[puzzleState.idx];
  if (!p) { endReview(); return; }
  puzzleState.current = p;
  puzzleState.answered = false;
  const turn = p.fen.split(' ')[1] === 'b' ? 'black' : 'white';
  puzzleState.board.set({
    fen: p.fen, orientation: turn, lastMove: undefined, drawable: { shapes: [] },
    movable: { free: true, color: turn, events: { after: onPuzzleMove } },
  });
  $('puzzleInfo').innerHTML =
    `<div>[${puzzleState.idx + 1}/${puzzleState.queue.length}] Puzzle #${p.id} — tema: ` +
    `<b>${NATURE_LABEL[p.theme] || p.theme || '–'}</b> (box ${p.box ?? 1})</div>` +
    `<div class="muted">Trova la mossa migliore muovendo un pezzo sulla scacchiera.</div>`;
}

async function gradePuzzle(uci) {
  puzzleState.answered = true;
  const p = puzzleState.current;
  const accepted = [p.solution_uci, ...(p.alternatives || [])];
  const ok = accepted.includes(uci);
  // mostra la soluzione con una freccia verde
  const sol = p.solution_uci;
  puzzleState.board.set({ drawable: { shapes: [{ orig: sol.slice(0, 2), dest: sol.slice(2, 4), brush: 'green' }] } });
  const solText = p.solution_san || p.solution_uci;
  $('puzzleInfo').innerHTML =
    `<div class="puzzle-result ${ok ? 'ok' : 'ko'}">${ok ? 'Corretto!' : 'Non è la migliore.'}</div>` +
    `<div>Soluzione: <b>${solText}</b>${p.explanation ? ` — ${p.explanation}` : ''}</div>` +
    `<div class="nav" style="justify-content:flex-start;"><button id="nextPuzzle" class="primary">Prossimo</button></div>`;
  try {
    await api(`/api/puzzles/${p.id}/review`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ correct: ok }),
    });
  } catch { /* il ripasso si aggiorna quando il server è raggiungibile */ }
  $('nextPuzzle').onclick = () => {
    puzzleState.idx += 1;
    if (puzzleState.idx >= puzzleState.queue.length) { loadPuzzles(); endReview(); }
    else showPuzzle();
  };
}

function endReview() {
  $('puzzleReview').hidden = true;
  puzzleState.current = null;
}

async function startReview() {
  const nick = $('profile').value;
  const q = new URLSearchParams({ due: 'true' });
  if (nick) q.set('user_id', nick);
  try {
    const { puzzles } = await api(`/api/puzzles?${q.toString()}`);
    if (!puzzles.length) { $('puzzleList').innerHTML = '<span class="muted">Nessun puzzle da ripassare.</span>'; return; }
    puzzleState.queue = puzzles; puzzleState.idx = 0;
    $('puzzleReview').hidden = false;
    showPuzzle();
    $('puzzleReview').scrollIntoView({ behavior: 'smooth' });
  } catch (e) {
    $('puzzleList').innerHTML = `<span style="color:var(--bad)">Errore: ${esc(e.message)}</span>`;
  }
}

async function loadPuzzles() {
  const nick = $('profile').value;
  const q = new URLSearchParams();
  if (nick) q.set('user_id', nick);
  try {
    const { puzzles } = await api(`/api/puzzles?${q.toString()}`);
    if (!puzzles.length) { $('puzzleList').innerHTML = '<span class="muted">Nessun puzzle generato: analizza una partita con errori.</span>'; return; }
    $('puzzleList').innerHTML = `<div class="muted">${puzzles.length} puzzle in archivio.</div>` +
      puzzles.slice(0, 12).map((p) =>
        `<div class="puzzle-item"><div><b>#${p.id}</b> · ${NATURE_LABEL[p.theme] || p.theme || '–'}
         <div class="muted">box ${p.box ?? 1} · dovuto ${(p.due_at || '').slice(0, 10)}</div></div>
         <div class="muted">${p.fen.slice(0, 24)}…</div></div>`).join('');
  } catch (e) {
    $('puzzleList').innerHTML = `<span style="color:var(--bad)">Errore: ${esc(e.message)}</span>`;
  }
}

// --------------------------------------------------------------------------- //
// FASE 8 — Gioco live su Lichess (§8.2) + blocco fair play (§8.3)
// --------------------------------------------------------------------------- //
const liveState = { board: null, poll: null, fen: START_FEN, myColor: null };

function initLiveBoard() {
  if (liveState.board) return;
  liveState.board = Chessground($('liveBoard'), {
    fen: START_FEN, orientation: 'white', coordinates: true,
    animation: { enabled: true, duration: 150 },
    // Solo il proprio lato è muovibile, e solo quando tocca a te.
    movable: { free: false, color: undefined },
    drawable: { enabled: false },
  });
}

function liveSetBoard(s) {
  const fen = s.fen && s.fen !== 'startpos' ? s.fen : START_FEN;
  liveState.fen = fen;
  liveState.myColor = s.my_color;
  const turn = fen.split(' ')[1] === 'w' ? 'white' : 'black';
  const myTurn = s.game_in_progress && s.my_color === turn;
  liveState.board.set({
    fen,
    orientation: s.my_color || 'white',
    lastMove: uciPair(s.last_move),
    movable: { free: false, color: myTurn ? s.my_color : undefined },
  });
  // Click-to-move: invia la mossa scelta.
  liveState.board.set({
    movable: {
      free: false,
      color: myTurn ? s.my_color : undefined,
      events: { after: (orig, dest) => sendLiveMove(orig, dest) },
    },
  });
}

async function sendLiveMove(orig, dest) {
  const uci = orig + dest;
  try { await api('/api/live/move', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uci }),
  }); } catch (e) { $('liveStatus').textContent = `Mossa rifiutata: ${e.message}`; }
  refreshLive();
}

function renderLive(s, gateInfo) {
  const banner = $('fairplayBanner');
  const playing = !!(gateInfo && gateInfo.game_in_progress);
  banner.innerHTML = playing
    ? '⛔ <b>Fair play attivo</b>: partita in corso — nessuna analisi è possibile (§8.3).'
    : 'Il blocco fair play è attivo: mentre giochi, <b>nessuna analisi</b> è possibile (§8.3).';
  banner.style.color = playing ? 'var(--bad)' : '';

  if (!s) { $('liveBoardPanel').hidden = true; $('liveStatus').textContent = 'Nessuna sessione live.'; return; }
  const label = { idle: 'pronto', searching: 'in ricerca…', playing: 'partita in corso', finished: 'finita' };
  $('liveStatus').textContent =
    `${label[s.status] || s.status}` + (s.opponent ? ` — vs ${s.opponent}` : '') +
    (s.message ? ` — ${s.message}` : '');
  $('liveBoardPanel').hidden = s.status === 'idle';
  if (s.status === 'idle') return;
  liveSetBoard(s);
  const cw = s.clock_white != null ? `${Math.round(s.clock_white)}s` : '–';
  const cb = s.clock_black != null ? `${Math.round(s.clock_black)}s` : '–';
  $('liveMeta').textContent = `tu: ${s.my_color || '?'} · clock B ${cw} / N ${cb}` +
    (s.result ? ` · ${s.result}` : '');
  $('liveMoves').textContent = (s.moves && s.moves.length) ? s.moves.join(' ') : '—';
}

async function refreshLive() {
  try {
    const data = await api('/api/live/state');
    liveState.myColor = data.state ? data.state.my_color : null;
    renderLive(data.state, data.gate);
  } catch (e) { /* rete assente: si riproverà */ }
}

function startLivePolling() {
  if (liveState.poll) return;
  liveState.poll = setInterval(refreshLive, 1000);
}

function wireLive() {
  $('tabLive').onclick = () => switchTab('live');
  $('liveSeek').onclick = async () => {
    const minutes = parseFloat($('liveMinutes').value) || 5;
    const increment = parseInt($('liveIncrement').value, 10) || 0;
    const rated = $('liveRated').checked;
    const profile = $('profile').value || null;
    try {
      await api('/api/live/seek', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ minutes, increment, rated, profile }),
      });
      startLivePolling();
      refreshLive();
    } catch (e) { $('liveStatus').textContent = `Errore: ${e.message}`; }
  };
  $('liveStop').onclick = () => { if (liveState.poll) { clearInterval(liveState.poll); liveState.poll = null; } };
  $('liveResign').onclick = async () => { await api('/api/live/resign', { method: 'POST' }); refreshLive(); };
  $('liveAbort').onclick = async () => { await api('/api/live/abort', { method: 'POST' }); refreshLive(); };
  $('liveDraw').onclick = async () => {
    await api('/api/live/draw', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accept: true }) });
    refreshLive();
  };
}

initBoard();
initPuzzleBoard();
initLiveBoard();
wireLive();
wire();
loadProfiles();
