# GM-Vibe — Guida completa

Questa guida spiega **cosa fa** GM-Vibe, **ogni sua funzionalità**, e **come è costruito**.
È pensata per chi usa l'applicazione (anche senza saper programmare) e per chi vuole
capire com'è fatta dentro: le tecnologie, le cartelle, il percorso di un dato dall'inizio
alla fine. Non serve conoscere gli scacchi a livello avanzato per leggerla.

---

## 1. Cos'è GM-Vibe, in breve

GM-Vibe è un **analizzatore di partite di scacchi di livello magistrale** che gira sul tuo
computer. Prende una partita (incollata come PGN, scaricata da Lichess/Chess.com, o giocata
dal vivo) e ne produce una **recensione da maestro**: dov'eri in vantaggio, quali mosse hai
sbagliato e *perché*, quanto era difficile la mossa giusta, quanto tempo hai perso, cosa
dice la teoria, e — se vuoi — un commento scritto in italiano da un'AI ("il Maestro
virtuale"). Poi **impara dalle tue partite**: accumula i tuoi errori, ti mostra le tue
debolezze ricorrenti e ti genera esercizi da ripassare.

**A chi serve:**
- a chi vuole capire *dove* perde punti e *perché*, non solo il numero di "imprecisioni";
- a chi ha un rating preciso (FIDE, Lichess, Chess.com) e vuole un'analisi **calibrata sul
  proprio livello** (un 1900 e un 2200 non ricevono la stessa analisi);
- a chi vuole esercitarsi **sui propri errori reali**, non su puzzle generici.

---

## 2. Il principio guida (la regola d'oro)

> **Il motore calcola, il codice deterministico misura, Claude spiega.**

Significa, in pratica:
1. **Il motore scacchistico** (Stockfish, e opzionalmente Maia) è l'unica fonte dei calcoli
   scacchistici: valutazioni, mosse migliori, probabilità.
2. **Il codice** (Python, senza AI) misura in modo ripetibile: applica formule, conta,
   confronta. Gli stessi input danno sempre gli stessi output.
3. **Claude (l'AI) spiega** soltanto: mette in parole ciò che il motore ha già trovato.
   Non gli è mai permesso *calcolare* scacchi, e **ogni variante che cita viene verificata**
   con python-chess prima di essere mostrata. Se sbaglia, si rigenera una volta; se sbaglia
   ancora, si mostra solo il dato del motore.

Questo evita l'errore tipico degli strumenti "AI per scacchi": numeri inventati e mosse
illegali. Qui l'AI non tocca i numeri.

---

## 3. Le tecnologie usate (e a cosa servono)

| Tecnologia | Cos'è, in parole semplici | A cosa serve in GM-Vibe |
|---|---|---|
| **Python 3.11** | Il linguaggio con cui è scritto tutto il programma. | È l'applicazione stessa: CLI, server, analisi. |
| **python-chess** | Una libreria Python che "capisce" gli scacchi: legge i PGN, conosce le regole, parla con i motori. | Parsing delle partite, legalità delle mosse, dialogo con Stockfish/Lc0, tablebase. |
| **Stockfish** | Il motore scacchistico open source più forte al mondo. | Calcola le valutazioni e la mossa migliore per ogni posizione. |
| **Lc0 + Maia** | Lc0 è un motore a reti neurali; **Maia** sono pesi addestrati a giocare *come un umano* di un certo livello. | Il "fattore 5" del Complexity Score: quanto è *naturale* per un umano la mossa giusta. **Opzionale**. |
| **SQLite** | Un database contenuto in un unico file, senza server da installare. | Salva la cache del motore (per non ricalcolare) e tutti i tuoi dati (analisi, errori, puzzle). |
| **Pydantic** | Una libreria che definisce "modelli di dati" e li valida automaticamente. | Garantisce che configurazioni e risultati abbiano sempre la forma giusta. |
| **PyYAML** | Legge i file di configurazione in formato YAML (leggibili a mano). | `levels.yaml`, `profile.yaml`, `rating_conversion.yaml`. |
| **FastAPI + uvicorn** | Un framework web Python e il suo "motore" che fa girare il sito. | La UI nel browser: analizza, esplora, ripassa, gioca. |
| **Chessground + chess.js** | La scacchiera interattiva di Lichess (grafica) e una libreria JS che conosce le regole. | Scacchiera navigabile nel browser, con frecce e mosse legali. |
| **API Anthropic (Claude)** | Il servizio AI di Anthropic. | Il "Maestro virtuale": scrive i commenti in italiano. **Opzionale**. |
| **urllib (stdlib)** | Il client HTTP incluso in Python. | Scarica partite da Lichess/Chess.com e interroga le tablebase. Nessuna dipendenza extra. |
| **dotenv + keyring** | Due modi per custodire le chiavi segrete. | Tengono i token fuori dal codice e dal repository. |
| **pytest** | Il framework di test Python. | Verifica automaticamente che tutto funzioni (271 test). |

Le "cose opzionali" (Maia, il Maestro virtuale) **non sono obbligatorie**: se mancano, GM-Vibe
funziona lo stesso e quella funzione si disattiva da sola, senza errori.

---

## 4. Come è organizzato (la mappa delle cartelle)

```
gmvibe/
├── levels.yaml            # I parametri per livello (soglie, momenti, Maia, live)
├── rating_conversion.yaml # Conversione approssimativa FIDE/Chess.com → Lichess
├── profiles/<nickname>/   # La tua configurazione: profile.yaml + secrets.env (segreta)
└── gmvibe/                # Il codice vero e proprio
    ├── config.py          # Carica profilo, livelli e segreti
    ├── level.py           # resolve_level: dal rating ai parametri (funzione pura)
    ├── models.py          # Tutti i "modelli di dati" (Pydantic)
    ├── metrics.py         # La formula di win%
    ├── db.py              # Il database SQLite (cache + dati utente)
    ├── fairplay.py        # La guardia "niente analisi durante una partita live"
    ├── engine/            # I ponti verso i motori: Stockfish e Maia/Lc0
    ├── sources/           # Lettura PGN, apertura, import da Lichess/Chess.com
    ├── lichess_live/      # Gioco dal vivo su Lichess (Board API)
    ├── analysis/          # Il cuore: la pipeline di analisi
    ├── profile/           # Analisi cross-partita: debolezze + puzzle
    ├── ai_coach/          # Il layer LLM (Claude) con validazione
    ├── web/               # Il server e la UI nel browser
    └── cli/               # I comandi da terminale (init, analyze, serve, …)
```

Ogni cartella ha una responsabilità sola. Le sezioni successive spiegano cosa fa ciascuna.

---

## 5. Le funzionalità, una per una

### 5.1 Analisi di una partita (il cuore)

È ciò che succede quando premi "Analizza". Il percorso, in ordine:

1. **Parsing** — la partita (PGN) viene letta: mosse, nomi, risultato, e i **tempi**
   (`[%clk]`, i secondi rimasti dopo ogni mossa). Si gestiscono anche partite che **non
   iniziano dalla posizione di partenza** (es. un finale, con `SetUp`/`FEN`).
2. **Pass veloce** — Stockfish valuta **ogni** posizione (a bassa profondità o ~0,3s). Da
   qui si ricava la curva di valutazione e la win% di ogni mossa.
3. **Selezione dei candidati** — non tutte le mosse meritano un'analisi profonda. Si
   scelgono quelle "sospette": cali di win%, vicinanza a un cambio di verdetto, tempo
   speso anomalo, posizioni critiche.
4. **Pass profondo** — solo sui candidati, Stockfish rianalizza **MultiPV=4** (le 4 mosse
   migliori) e rivaluta *esattamente la mossa giocata*, per un confronto onesto.
5. **Metriche** — si calcola la **win%** (una probabilità di vittoria da 0 a 100, più
   sensata dei centipedoni), il **calo di win%** (quanto hai perso con quella mossa), e
   l'**accuratezza** totale e per fase (apertura / mediogioco / finale).
6. **Classificazione** — ogni mossa diventa *ok*, *imprecisione*, *errore* o *svarione*,
   secondo soglie che dipendono dal tuo livello.

**Perché la win% e non i centipedoni?** Un calo di 1,5 pedoni in una posizione già vinta
(+8) non è un errore; lo stesso calo in una posizione pari è grave. La win% cattura questo:
`win%(cp) = 50 + 50·(2/(1+e^(−0,00368208·cp)) − 1)` — la stessa formula di Lichess.

### 5.2 Feature posizionali e "natura" dell'errore

Sapere *quanto* un errore è costato non basta: serve sapere **che tipo** di errore è.

- **Feature posizionali** (calcolate con python-chess, deterministiche): pedoni isolati /
  doppiati / arretrati / passati, pedone di donna isolato (IQP), pedoni sospesi, avamposti,
  colonne aperte, sicurezza del Re (scudo di pedoni), mobilità, coppia di alfieri — sia
  prima che dopo la mossa, con la differenza.
- **Natura dell'errore** (§5.G): ogni errore riceve un'**etichetta** tra:
  `tempo`, `tattica_mancata`, `pezzo_in_presa`, `valutazione_errata`, `piano_sbagliato`,
  `profilassi_mancata`, `sicurezza_re`, `tecnica_conversione`, `tecnica_difesa`,
  `finale_tecnica`, `teoria`. Le regole sono ordinate per priorità e le soglie stanno in
  `levels.yaml`, mai nel codice.

### 5.3 Complexity Score (quanto era difficile la mossa giusta)

Un punteggio **0-100** che misura la difficoltà della posizione, non della tua mossa.
Combina cinque fattori (pesati da `levels.yaml`):

1. **Gap** — distanza in win% tra la mossa migliore e la seconda.
2. **Mosse ragionevoli** — quante mosse stanno vicine alla migliore (poche = "solo mossa").
3. **Instabilità in profondità** — la mossa migliore cambia se il motore guarda più a fondo?
4. **Controintuitività** — la mossa giusta è "silenziosa", un ritiro o un sacrificio,
   contro l'istinto di catturare/attaccare?
5. **Maia** *(opzionale)* — quanto è **poco naturale** per un umano del tuo livello la mossa
   giusta. Se Maia non è installato, questo fattore semplicemente non entra.

### 5.4 Momenti critici

Dalle mosse analizzate si estraggono i **5-7 momenti** più importanti della partita (il
numero dipende dal livello). Per ognuno: la posizione, la mossa giocata, la mossa giusta,
l'etichetta dell'errore e le varianti del motore. Sono i momenti che poi il Maestro
virtuale commenta. Nel report compaiono con la mini-scacchiera e le frecce.

### 5.5 Analisi del tempo (zeitnot)

Usando i clock del PGN, si misura **quanto tempo** hai speso per mossa e si individuano:
- le mosse giocate in **zeitnot** (pochissimo tempo),
- il tempo speso su mosse **banali**,
- le differenze tra te e l'avversario, per cadenza (blitz/rapid/classica).

Un errore "da tempo" è spesso un errore di *gestione*, non di conoscenza — e va allenato
in modo diverso.

### 5.6 Finali con le tablebase

Nei finali con **pochi pezzi** (≤7) esiste il verdetto **esatto**: vinto, patta o perso,
calcolato alla perfezione. GM-Vibe lo confronta con la valutazione del motore: dove il
motore "sperava" e la tablebase dice "patta", o viceversa. Usa le tablebase Syzygy locali
(se presenti) e l'API tablebase di Lichess.

### 5.7 Apertura e teoria

- Si riconosce l'**apertura** (nome + codice ECO) da un dataset incluso.
- Si trova **dove sei uscito dalla teoria** (l'ultima mossa ancora "da libro").
- Se hai un token Lichess, si confrontano le **statistiche reali** di quella posizione.

### 5.8 Il Maestro virtuale (Claude, opzionale)

Se hai una chiave Anthropic, il pulsante **"Analizza col Maestro"** fa scrivere a Claude un
report in italiano: sintesi della partita, commento dei momenti critici, regola di pensiero,
lezione finale e 3 cose da allenare.

Le garanzie (§6):
- Claude riceve **solo un pacchetto JSON strutturato** con i momenti — **mai il PGN intero**;
- **ogni variante citata viene validata** con python-chess (legalità, ancoraggio alla
  posizione): se una non regge, si rigenera una volta; se ancora no, si mostra solo il dato
  del motore con un avviso;
- il **costo in token** per partita viene registrato e mostrato.

### 5.9 Import da Lichess, Chess.com e PGN — e analisi in batch

- **Lichess**: scarica le ultime N partite (con clock). Le partite pubbliche **non
  richiedono token**: basta il nickname. Il token serve solo per partite private.
- **Chess.com**: scarica dagli archivi mensili pubblici.
- **PGN**: uno o più file, anche multi-partita (importante per le partite **OTB di
  torneo**, che vanno analizzate con la stessa pipeline ma senza assumere zeitnot da blitz).
- Ogni partita viene **analizzata in batch** e salvata: analisi completa, PGN grezzo (per
  ri-analisi e deduplica), **ogni errore etichettato** e i puzzle generati. Le partite già
  viste non si ricalcolano (deduplica per impronta del PGN).

### 5.10 Profilo di debolezze (l'analisi cross-partita)

Ecco il vero vantaggio rispetto a un'analisi partita-per-partita. Il comando `profile`
(misura tutte le ultime N partite insieme) mostra:
- le **etichette più frequenti** ("tecnica_conversione: 7 su 20 partite");
- l'**accuratezza** per fase, per colore, per cadenza e per apertura;
- gli **errori in funzione del tempo** (fasce di clock);
- il **repertorio**: quali aperture ti fanno perdere;
- l'**evoluzione** nel tempo (stai migliorando, stabile, o peggiorando);
- **3 azioni concrete "da allenare"**.

E nel report di ogni partita, i momenti già visti in passato portano il badge
**"già comparso in X partite"**.

### 5.11 Puzzle e ripasso a ripetizione dilazionata

Dai tuoi errori reali GM-Vibe genera **puzzle**: la posizione prima dell'errore, e come
soluzione **la mossa migliore del motore** (mai una mossa inventata dall'AI). La spiegazione
è deterministica (natura dell'errore + fatti posizionali).

Il ripasso usa il metodo **Leitner** (ripetizione dilazionata): ogni puzzle ha un "box"
(1→5); rispondi bene e sale di box (rivedendolo dopo 1, 3, 7, 16, 35 giorni), sbagli e
torna al box 1. Intervalli configurabili in `levels.yaml`. Si ripassa da terminale
(`review`) o nel browser con la scacchiera.

### 5.12 Maia (opzionale)

Se installi Lc0 con i pesi Maia, l'analisi aggiunge il **fattore 5** del Complexity Score:
la probabilità che un umano del tuo livello giochi la mossa giusta. Serve a distinguere
"mossa difficile per un computer" da "mossa davvero poco intuitiva per un umano".

### 5.13 Gioco live su Lichess + blocco fair play

Dalla scheda **Gioca** (o dal comando `live`) GM-Vibe cerca una partita su Lichess
(blitz/rapid/classica; **il bullet non è supportato**) e la giochi nel browser. Serve un
token personale con permesso `board:play`.

**Regola di fair play (§8.3, inderogabile):** mentre una partita è in corso **nessun
accesso a motore, valutazioni o Claude**. Non è nascosto nella grafica: è **imposto dal
codice**. Ogni endpoint di analisi risponde **409** e le funzioni di analisi rifiutano di
partire; l'analisi si sblocca **solo a partita finita**. Usare un motore durante una partita
rated viola le regole di Lichess e può portare al ban.

### 5.14 Sicurezza e segreti

- **Mai password** di Lichess/Chess.com: solo token.
- I segreti (chiave Anthropic, token Lichess) stanno in `profiles/<nick>/secrets.env`
  (permessi 600, escluso dal repository) o nel **keyring** di sistema — mai nel codice, nei
  log o nel database.
- **Nessun dato personale è scritto nel codice**: rating, nickname e percorsi vengono dal
  profilo e da `levels.yaml`.
- Ogni tabella di dati utente ha un `user_id`; la cache del motore è invece **condivisa**.

---

## 6. Il viaggio di una partita (flusso completo)

```
PGN / Lichess / Chess.com / partita live
        │
        ▼
   Parsing (mosse + clock + eventuale FEN di partenza)
        │
        ▼
   Pass veloce (Stockfish su ogni posizione)  ──►  cache SQLite (riuso)
        │
        ▼
   Selezione candidati
        │
        ▼
   Pass profondo MultiPV=4 sui candidati
        │
        ▼
   Metriche: win%, calo, accuratezza · Feature · Natura errore · Complexity (+Maia)
        │
        ▼
   Momenti critici · Apertura · Tempo · Finale (tablebase)
        │
        ├──►  Report + UI nel browser (scacchiera, grafici, frecce)
        │
        ├──►  Maestro virtuale (Claude, solo se richiesto e validato)
        │
        └──►  Salvataggio: analisi + errori etichettati + puzzle
                               │
                               ▼
                     Profilo di debolezze  +  Ripasso dilazionato
```

---

## 7. Profili e configurazione

Tre file (nessuno contiene segreti, tranne quello apposta):

- **`profiles/<nickname>/profile.yaml`** — chi sei: nickname, rating, tipo di rating
  (FIDE / Lichess / Chess.com) e cadenza preferita. **Inserisci solo il rating**: il resto
  dei parametri viene calcolato.
- **`profiles/<nickname>/secrets.env`** — i segreti (chiave Anthropic, token Lichess).
  Permessi 600, mai nel repository.
- **`levels.yaml`** — il "cervello delle soglie": cosa cambia al variare del livello. Definisce
  punti di ancoraggio (es. a 1400, 1700, 2000, 2300) e GM-Vibe **interpola** tra loro: un
  1950 ottiene valori intermedi. Contiene anche le soglie di errore, i pesi del Complexity
  Score, i parametri di Maia e del gioco live. **Nessuna soglia è scritta nel codice.**

Variabili d'ambiente utili (facoltative): `GMVIBE_STOCKFISH` (percorso di Stockfish),
`GMVIBE_LC0` (percorso di Lc0), `GMVIBE_DB` (percorso del database),
`GMVIBE_COACH_MODEL` / `GMVIBE_COACH_MODEL_FAST` (modelli Claude).

---

## 8. Installazione e comandi

**Installazione (5 comandi):**
```bash
sudo apt install -y stockfish                 # il motore
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,web]"                   # [llm] in più per il Maestro virtuale
python -m gmvibe init                         # wizard guidato: crea profiles/<nickname>/
python -m gmvibe analyze partita.pgn --profile <nickname>
```

**I comandi da terminale:**

| Comando | Cosa fa |
|---|---|
| `gmvibe init` | Wizard di configurazione (crea il tuo profilo). |
| `gmvibe analyze partita.pgn` | Analizza una partita e stampa la tabella mossa per mossa. |
| `gmvibe analyze … --depth N` | Profondità del pass veloce. |
| `gmvibe analyze … --no-deep` | Salta il pass profondo (più veloce). |
| `gmvibe analyze … --refresh` | Ignora la cache e ricalcola. |
| `gmvibe analyze … --coach` | Aggiunge il report del Maestro virtuale (Claude). |
| `gmvibe analyze … --save` | Salva analisi, errori e puzzle nel database. |
| `gmvibe analyze … --maia` | Usa Maia per il fattore 5 (se installato). |
| `gmvibe import --lichess <user> --max 20` | Scarica le ultime 20 partite e le analizza. |
| `gmvibe import --chesscom <user> --max 20` | Idem, dagli archivi mensili Chess.com. |
| `gmvibe import --pgn a.pgn b.pgn` | Analizza uno o più file PGN (anche OTB). |
| `gmvibe profile --last 20` | Profilo di debolezze sulle ultime 20 partite. |
| `gmvibe puzzles --due` | Puzzle da ripassare. |
| `gmvibe review` | Sessione di ripasso dilazionato da terminale. |
| `gmvibe live --profile <nick>` | Gioca una partita live su Lichess. |
| `gmvibe serve` | Apre la UI nel browser (`127.0.0.1:8000`). |

**La UI nel browser** ha tre schede:
- **Partita** — incolla un PGN e premi Analizza: curva di valutazione, tempo, momenti
  critici con scacchiera navigabile (frecce + tasti), mosse buone, finale, e il pulsante per
  il Maestro virtuale.
- **Profilo & Puzzle** — il profilo di debolezze e il ripasso degli esercizi.
- **Gioca** — la partita live su Lichess, con il banner del blocco fair play.

---

## 9. Glossario

- **PGN** — il formato di testo standard per salvare una partita di scacchi.
- **FEN** — una stringa che descrive una singola posizione sulla scacchiera.
- **Motore** — un programma che calcola le mosse migliori (Stockfish, Lc0/Maia).
- **Win%** — probabilità di vittoria (0-100) ricavata dalla valutazione del motore.
- **Centipedone (cp)** — l'unità classica di valutazione (100 cp = 1 pedone).
- **MultiPV** — far calcolare al motore le N mosse migliori, non solo la prima.
- **Tablebase** — un database che conosce il verdetto esatto (vinto/patta/perso) dei finali
  con pochi pezzi.
- **ECO** — il codice standard che identifica un'apertura (es. C50 = "Italian Game").
- **Zeitnot** — giocare con pochissimo tempo residuo.
- **Leitner / ripetizione dilazionata** — tecnica di studio che ripresenta un esercizio a
  intervalli crescenti, per fissarlo in memoria.
- **Fair play** — la regola che vieta di usare un motore durante una partita: GM-Vibe la
  impone nel codice.

---

## 10. Limiti noti e funzioni opzionali

- **Complessità della partita**: un'analisi profonda (`deep`) è **lenta** (minuti per
  partita con Stockfish a un solo thread). Per prove rapide si usa `--no-deep`.
- **Il bullet non è supportato** dalla Board API di Lichess (blitz/rapid/classica sì).
- **Maia** richiede Lc0 e pesi scaricati a parte (non inclusi); il formato dell'output di
  Lc0 è gestito con un parser tollerante ma va validato sull'installazione reale.
- **Il Maestro virtuale** richiede la chiave Anthropic e il pacchetto `anthropic`.
- **Tablebase/Opening Explorer di Lichess**: alcune funzioni di rete richiedono un token;
  senza, si usano il dataset offline e le Syzygy locali.
- **Rinviati/opzionali**: esportazione dei puzzle verso Anki/Lichess study, e la "modalità
  cooldown" (§8.4, il pulsante "Nuova partita" bloccato finché non leggi il report).
- **Fair play**: la regola blocca l'*esecuzione* di analisi durante la partita; la lettura
  di un report di un'*altra* partita già salvato resta permessa.
