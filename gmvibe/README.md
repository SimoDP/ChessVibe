# GM-Vibe

Analisi scacchistica di livello magistrale. **FASE 1-8 completate** (motore e dati, pass
profondo e metriche, feature posizionali e tipo d'errore, tablebase/apertura/tempo,
layer LLM con validazione, server + UI di review nel browser, import Lichess/Chess.com e
profilo cross-partita con puzzle ripassati a ripetizione dilazionata, Maia via Lc0 nel
Complexity Score e gioco live su Lichess con blocco fair play).

> Il motore calcola, il codice deterministico misura, Claude spiega.

📖 **Vuoi capire cosa fa ogni funzione e con quali tecnologie è costruita?**
Leggi la [**GUIDA.md**](GUIDA.md): una spiegazione completa, pensata anche per chi non
programma.

## Installazione (5 comandi)

```bash
sudo apt install -y stockfish            # o scarica un binario ufficiale e imposta GMVIBE_STOCKFISH
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,web]"              # [web] = FastAPI+uvicorn; [llm] per il Maestro virtuale
python -m gmvibe init                    # wizard: crea profiles/<nickname>/
python -m gmvibe analyze tests/fixtures/sample_short.pgn --profile <nickname>
```

Su Debian/Ubuntu Stockfish finisce in `/usr/games/stockfish`: l'app lo rileva da sola
(oppure imposta `GMVIBE_STOCKFISH=/percorso/stockfish`).

## Uso

```bash
python -m gmvibe init                                   # configurazione guidata (§4.5)
python -m gmvibe analyze partita.pgn [--profile simo]   # tabella mossa per mossa
python -m gmvibe analyze partita.pgn --depth 14         # profondita' del pass veloce
python -m gmvibe analyze partita.pgn --refresh          # ignora la cache
python -m gmvibe analyze partita.pgn --coach            # + Maestro virtuale (layer LLM, §6)
python -m gmvibe analyze partita.pgn --save             # salva analisi/errori/puzzle nel DB (§7)
```

### Import e analisi cross-partita (§7, §8.1)

```bash
python -m gmvibe import --lichess <username> --max 20   # ultime 20 partite (pubbliche)
python -m gmvibe import --chesscom <username> --max 20  # archivi mensili Chess.com
python -m gmvibe import --pgn mie.pgn altra.pgn         # uno o più file PGN (anche multi-partita)
python -m gmvibe profile --profile <nick> --last 20     # profilo di debolezze (§7)
python -m gmvibe puzzles --due                          # puzzle generati dagli errori (§7.1)
python -m gmvibe review                                 # sessione di ripasso (Leitner)
```

### Maia e gioco live (§5.H, §8)

```bash
python -m gmvibe analyze partita.pgn --maia             # fattore 5: probabilità umana (Maia/Lc0)
python -m gmvibe live --profile <nick>                  # gioca una partita live su Lichess
```

**Maia** (opzionale, §5.H): se Lc0 e i pesi Maia sono installati, il Complexity Score
aggiunge il *fattore 5* — quanto è poco "naturale" per un umano del tuo livello la mossa
migliore del motore. Percorsi e opzioni stanno in `levels.yaml` (`maia`), mai nel codice;
senza Lc0/pesi il fattore si disattiva e il punteggio resta quello dei fattori 1-4.

**Gioco live** (§8.2): `live` cerca una partita con la Board API (serve un token Lichess
con scope `board:play` in `profiles/<nick>/secrets.env`; mai una password). **Regola di
fair play inderogabile** (§8.3): mentre una partita è in corso l'accesso a motore,
valutazioni e Claude è **impossibile** — non solo nascosto nella UI, ma imposto nel codice
(`gmvibe/fairplay.py`): ogni endpoint di analisi risponde **409** e le funzioni
`analyze_game`/`coach_analysis` rifiutano di partire. La Board API non supporta il bullet.

`import` scarica (o legge) le partite, le **analizza in batch** e salva per ognuna: analisi
completa, PGN grezzo (per ri-analisi/dedup), **ogni errore etichettato** e i **puzzle**
generati. Il profilo mostra le etichette più frequenti ("tecnica_conversione: 7 su 20
partite"), l'accuratezza per fase/colore/cadenza/apertura, gli errori in funzione del
clock, il repertorio e la tendenza nel tempo; ogni report di partita segnala "questo errore
è già comparso in X partite precedenti". Le partite importate richiedono rete solo al
momento del download: l'analisi è locale.

Nessuna password: per Lichess basta lo **username** (il token solo per partite private);
per Chess.com solo lo username (API pubblica). I puzzle usano la **mossa migliore del
motore**, mai una mossa generata dall'LLM.

Il layer LLM richiede la chiave Anthropic e il pacchetto omonimo:

```bash
pip install -e ".[llm]"
# poi inserisci ANTHROPIC_API_KEY in profiles/<nickname>/secrets.env (o keyring)
```

`--coach` seleziona al massimo 5-7 momenti critici (§5.I), invia a Claude **solo** il
pacchetto JSON strutturato (§6.1, mai il PGN intero) e **valida ogni variante citata**
con python-chess (§6.4): se l'output non è legale rigenera una volta, altrimenti mostra
i soli dati del motore. Costo in token e varianti legali sono stampati a fine report.

Output: riga di calibrazione + `nº | colore | SAN | eval | win% | Δwin% | clock | apertura`,
seguita da mosse totali, cache hit/miss e tempo. La **seconda esecuzione** sulla stessa
partita e' quasi istantanea (cache SQLite condivisa, chiave `fen+depth+multipv+versione_motore`).

## UI di review (browser)

```bash
pip install -e ".[web]"
python -m gmvibe serve                 # http://127.0.0.1:8000  (--host/--port/--reload)
```

Incolla un PGN, premi **Analizza**: l'analisi gira in background (job + polling, §3) e
la pagina mostra curva di valutazione, tempo, apertura, momenti critici con scacchiera
Chessground navigabile (frecce + frecce tastiera), mosse buone, finale e — col pulsante
**Analizza col Maestro** — il report del coach (§6, con degrado pulito se manca la chiave
Anthropic). Chessground e chess.js sono **vendorizzati** in `gmvibe/web/static/vendor/`:
nessuna dipendenza da CDN, l'app funziona offline.

La scheda **Profilo & Puzzle** mostra il profilo di debolezze cross-partita (§7) e il
ripasso degli esercizi (§7.1): scegli un profilo, "Aggiorna" per il profilo, "Ripassa i
dovuti" per una sessione Leitner sulla scacchiera (giochi la mossa, il confronto con la
soluzione del motore aggiorna il box). Nel report partita, i momenti già visti in passato
portano il badge "già comparso in X partite".

La scheda **Gioca** (§8.2) cerca una partita su Lichess e la gioca sulla scacchiera. In
alto un banner ricorda che il **blocco fair play** è attivo: finché la partita è in corso
ogni analisi è rifiutata dal server (§8.3).

## Struttura

```
gmvibe/
├── levels.yaml               # punti di ancoraggio dei parametri per livello (§4.2/§4.3)
├── rating_conversion.yaml    # conversione approssimativa FIDE/Chess.com -> Lichess (§4.3.1)
├── secrets.example.env       # solo i nomi dei campi segreti
├── profiles/<nickname>/      # profile.yaml + secrets.env (git-ignored)
└── gmvibe/
    ├── config.py             # caricamento profilo/livelli/segreti
    ├── level.py              # resolve_level (funzione pura)
    ├── models.py             # modelli Pydantic
    ├── metrics.py            # formula win% (§5.E)
    ├── db.py                 # SQLite: cache motore + dati utente
    ├── fairplay.py           # guardia §8.3: nessuna analisi durante una partita live
    ├── engine/               # wrapper Stockfish + Maia via Lc0 (FASE 8)
    ├── sources/              # PGN + clock, apertura, import Lichess/Chess.com (FASE 7)
    ├── lichess_live/         # Board API + sessione di gioco live (FASE 8)
    ├── analysis/             # pipeline, candidates, metrics, complexity, features,
    │                         #   error_taxonomy, time_analysis, store, batch (FASE 7)
    ├── profile/              # cross-partita: weaknesses (§7), puzzles + Leitner (§7.1)
    ├── ai_coach/             # layer LLM: moments, context, client, validators (FASE 5)
    │   └── prompts/          #   system_it.md (§6.2) + coach_analysis.md
    ├── web/                  # server FastAPI + UI di review (FASE 6)
    │   ├── app.py            #   create_app (route API + pagina, static mount)
    │   ├── jobs.py           #   coda in-process (thread) per analisi/LLM
    │   └── static/           #   index.html, app.js, style.css + vendor/ (Chessground, chess.js)
    └── cli/                  # init, analyze (+ --coach/--save/--maia), serve,
                              #   import, profile, puzzles, review, live (FASE 7-8)
```

## Test

```bash
pytest -q              # unit test (nessun motore richiesto)
pytest -q -m slow      # integrazione con Stockfish reale
```

## Sicurezza

- Nessuna password di Lichess/Chess.com, mai: solo token. I segreti stanno in
  `profiles/<nickname>/secrets.env` (chmod 600, git-ignored) o nel keyring di sistema.
- Nessun Elo, username o path e' scritto nel codice: tutto viene dal profilo e da `levels.yaml`.
- **Fair play (§8.3)**: nessun accesso a motore/valutazioni/Claude durante una partita
  live. La regola e' imposta dal codice (`gmvibe/fairplay.py`), non dalla UI.
