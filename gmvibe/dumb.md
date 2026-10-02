# GM-Vibe: guida per iniziare senza essere tecnici

Questa guida spiega come installare e usare GM-Vibe su Linux/Ubuntu, anche se non hai mai usato Python o il terminale.

GM-Vibe analizza le tue partite di scacchi con Stockfish e ti mostra errori, momenti critici, tempi e debolezze ricorrenti. Puoi usarlo dal browser, senza dover imparare i comandi a memoria.

## 1. Cosa serve

Ti servono:

- un computer Linux/Ubuntu;
- una connessione internet per l'installazione e per scaricare partite da Lichess o Chess.com;
- Python 3.11 o più recente;
- Stockfish, il motore che calcola le mosse;
- una partita in formato `.pgn`, oppure un account Lichess/Chess.com.

Non servono:

- un account a pagamento;
- la password di Lichess o Chess.com;
- Claude o Maia per l'analisi normale: sono funzioni opzionali.

## 2. Aprire il terminale

Il terminale è la finestra in cui si incollano i comandi.

Su Ubuntu puoi aprirlo premendo `Ctrl` + `Alt` + `T`.

Quando questa guida dice di eseguire un comando:

1. copia la riga;
2. incollala nel terminale;
3. premi Invio;
4. aspetta che finisca.

Non copiare il simbolo `$`, se compare negli esempi.

## 3. Installare GM-Vibe

### 3.1 Entrare nella cartella del progetto

Nel terminale esegui:

```bash
cd /home/simonedalprete/Documents/finalegm/gmvibe
```

Se il progetto si trova in un'altra cartella, sostituisci il percorso con quello corretto.

### 3.2 Installare Python e Stockfish

Su Ubuntu esegui:

```bash
sudo apt update
sudo apt install -y python3 python3-venv stockfish
```

La password chiesta da `sudo` è la password del tuo computer. Mentre la scrivi non vedrai comparire caratteri: è normale.

### 3.3 Creare l'ambiente del progetto

L'ambiente virtuale tiene le librerie di GM-Vibe separate dal resto del computer. Va creato una sola volta:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,web]"
```

Quando l'ambiente è attivo, all'inizio della riga del terminale vedrai probabilmente `(.venv)`.

Ogni volta che riapri il terminale, torna nella cartella e riattivalo:

```bash
cd /home/simonedalprete/Documents/finalegm/gmvibe
source .venv/bin/activate
```

## 4. Configurare il proprio profilo

Il profilo dice a GM-Vibe chi sei e quale rating usare per calibrare l'analisi.

Esegui:

```bash
python -m gmvibe init
```

Il programma farà alcune domande. Puoi rispondere così:

- **Nickname**: il nome con cui vuoi riconoscere il profilo, per esempio `mario`;
- **Lingua del report**: lascia `it`;
- **Rating**: il tuo rating attuale;
- **Tipo di rating**: scegli `fide`, `lichess`, `chesscom` oppure `altro`;
- **Cadenza preferita**: scegli `blitz`, `rapid` oppure `classica`;
- **Username Lichess/Chess.com**: puoi lasciarli vuoti, oppure inserirli se vuoi importare partite;
- **Provider LLM e chiave**: puoi lasciare la chiave vuota; serve solo per il commento del Maestro virtuale;
- **Lichess token**: lascia vuoto, a meno che tu voglia giocare una partita live dalla scheda Gioca.

Quando il programma mostra la calibrazione, rispondi `s` per confermare.

Il wizard crea automaticamente la cartella `profiles/<nickname>` e salva lì la configurazione. Se trova Stockfish, può anche analizzare una partita di esempio: conferma con `s` per verificare subito che tutto funzioni.

## 5. Avviare l'interfaccia nel browser

Questo è il modo consigliato per usare GM-Vibe.

Nel terminale, con `(.venv)` attivo, esegui:

```bash
python -m gmvibe serve
```

Poi apri il browser e vai a:

```text
http://127.0.0.1:8000
```

Il terminale deve rimanere aperto mentre usi GM-Vibe. Per chiudere il programma torna al terminale e premi `Ctrl` + `C`.

## 6. Analizzare una partita dal browser

Nella pagina di GM-Vibe:

1. apri la scheda di analisi;
2. incolla il testo PGN della partita;
3. premi **Analizza**;
4. aspetta il completamento dell'analisi;
5. esplora la curva di valutazione, la scacchiera, i momenti critici, i tempi e gli errori.

L'analisi può richiedere tempo, soprattutto la prima volta. Le analisi successive della stessa posizione sono più veloci perché GM-Vibe usa una cache locale.

La scheda **Analizza col Maestro** è opzionale: per usarla devi avere una chiave Anthropic o OpenRouter configurata. Il motore scacchistico calcola le mosse; l'AI serve solo a spiegarle in parole.

## 7. Analizzare un file PGN dal terminale

Se hai un file, per esempio `partita.pgn`, mettilo nella cartella del progetto e usa:

```bash
python -m gmvibe analyze partita.pgn --profile mario
```

Sostituisci `mario` con il nickname scelto durante `init`.

Varianti utili:

```bash
# Analisi più veloce
python -m gmvibe analyze partita.pgn --profile mario --no-deep

# Ricalcolare ignorando i risultati già in cache
python -m gmvibe analyze partita.pgn --profile mario --refresh

# Salvare analisi, errori e puzzle nel database
python -m gmvibe analyze partita.pgn --profile mario --save
```

## 8. Importare le proprie partite

### Da Lichess

Per le partite pubbliche basta lo username: non serve la password né un token.

```bash
python -m gmvibe import --lichess mario_lichess --max 20 --profile mario
```

`--max 20` significa “importa al massimo le ultime 20 partite”. Puoi cambiare il numero.

### Da Chess.com

Anche qui basta lo username per gli archivi pubblici:

```bash
python -m gmvibe import --chesscom mario_chesscom --max 20 --profile mario
```

### Da uno o più file PGN

```bash
python -m gmvibe import --pgn partita1.pgn partita2.pgn --profile mario
```

L'importazione analizza le partite e salva i risultati. Le partite già riconosciute non vengono normalmente ricalcolate.

## 9. Vedere le proprie debolezze e fare i puzzle

Dopo aver importato o salvato alcune partite:

```bash
# Report complessivo sulle ultime 20 partite
python -m gmvibe profile --profile mario --last 20

# Elenco dei puzzle da ripassare oggi
python -m gmvibe puzzles --profile mario --due

# Sessione di ripasso nel terminale
python -m gmvibe review --profile mario
```

Nel profilo puoi vedere, tra le altre cose, quali tipi di errore si ripetono, in quali fasi giochi peggio, come incidono i tempi e quali aperture ti creano più problemi.

## 10. Funzioni opzionali

### Maestro virtuale

Per aggiungere commenti scritti da Claude:

```bash
python -m pip install -e ".[llm]"
```

Poi inserisci la chiave nel profilo durante `python -m gmvibe init`, oppure nel file `profiles/mario/secrets.env`:

```env
ANTHROPIC_API_KEY=la_tua_chiave
```

Non condividere mai questo file e non inserire la chiave nel codice.

### Usare OpenRouter e scegliere il modello

OpenRouter permette di usare modelli di fornitori diversi con una sola API key. Per configurarlo:

1. crea un account su OpenRouter e genera una chiave API;
2. installa il supporto LLM, se non lo hai già fatto:

	```bash
	python -m pip install -e ".[llm]"
	```

3. apri il file `profiles/mario/secrets.env` e aggiungi la chiave:

	```env
	OPENROUTER_API_KEY=la_tua_chiave_openrouter
	```

	Sostituisci `mario` con il nome della tua cartella profilo. Se il file non esiste, esegui prima `python -m gmvibe init` e scegli `openrouter` quando viene chiesto il provider LLM.

4. apri il file `levels.yaml` nella cartella principale del progetto e trova la sezione `coach`;
5. imposta `provider: openrouter` e scrivi gli slug dei modelli desiderati nella sezione `openrouter`:

	```yaml
	coach:
	  provider: openrouter
	  openrouter:
		 model_main: anthropic/claude-sonnet-4.5
		 model_fast: openai/gpt-4o-mini
	```

	`model_main` viene usato per il commento completo della partita. `model_fast` viene usato per le operazioni più leggere. Gli slug disponibili sono mostrati nella pagina del modello su OpenRouter e hanno normalmente il formato `fornitore/nome-modello`.

Non modificare `base_url`: deve rimanere `https://openrouter.ai/api/v1`.

#### Scegliere un modello solo per una sessione

Per provare un modello senza modificare `levels.yaml`, apri un nuovo terminale, entra nella cartella del progetto, attiva l'ambiente virtuale e imposta queste variabili prima di avviare GM-Vibe:

```bash
export GMVIBE_COACH_PROVIDER=openrouter
export GMVIBE_OPENROUTER_MODEL=anthropic/claude-sonnet-4.5
export GMVIBE_OPENROUTER_MODEL_FAST=openai/gpt-4o-mini
python -m gmvibe serve
```

Queste impostazioni valgono solo per quel terminale e spariscono quando lo chiudi. Per renderle permanenti, usa invece la modifica a `levels.yaml` descritta sopra.

La chiave OpenRouter deve avere credito o un metodo di pagamento valido secondo il tuo account OpenRouter. Non pubblicare mai `secrets.env` e non incollare la chiave nei messaggi o nei log.

### Maia

Maia/Lc0 è facoltativo e aggiunge un fattore sulla naturalezza della mossa per un giocatore umano. Se non è installato, l'analisi normale continua a funzionare senza quel fattore.

### Giocare live su Lichess

Serve un token Lichess con permesso `board:play`, non la password:

```bash
python -m gmvibe live --profile mario
```

Durante una partita live GM-Vibe blocca volutamente motore, valutazioni e Maestro virtuale per rispettare il fair play. Il bullet non è supportato.

## 11. Problemi comuni

### “Stockfish non trovato”

Installa Stockfish:

```bash
sudo apt install -y stockfish
```

Se è già installato in un percorso particolare, puoi indicarlo così:

```bash
export GMVIBE_STOCKFISH=/percorso/completo/stockfish
```

### “No module named gmvibe”

Probabilmente l'ambiente virtuale non è attivo. Esegui:

```bash
cd /home/simonedalprete/Documents/finalegm/gmvibe
source .venv/bin/activate
python -m pip install -e ".[dev,web]"
```

### La pagina non si apre

Controlla che il comando `python -m gmvibe serve` sia ancora in esecuzione e prova a visitare `http://127.0.0.1:8000` esattamente così.

Se la porta è occupata, usa un'altra porta:

```bash
python -m gmvibe serve --port 8001
```

Poi apri `http://127.0.0.1:8001`.

### L'analisi è lenta

È normale per partite lunghe o analisi profonde. Puoi fare un primo controllo rapido con:

```bash
python -m gmvibe analyze partita.pgn --profile mario --no-deep
```

La cache rende più veloci le analisi ripetute della stessa partita.

### Ho dimenticato il nickname del profilo

Guarda le cartelle dentro `profiles`:

```bash
ls profiles
```

Il nome della cartella è il nickname da usare con `--profile`.

## 12. Controllare che l'installazione sia corretta

Per eseguire i test automatici:

```bash
python -m pytest -q
```

Se vuoi solo fare una verifica veloce, il wizard `python -m gmvibe init` e l'analisi della partita di esempio sono sufficienti per controllare profilo, Stockfish e pipeline principale.

## 13. La routine consigliata

Per l'uso quotidiano:

```bash
cd /home/simonedalprete/Documents/finalegm/gmvibe
source .venv/bin/activate
python -m gmvibe serve
```

Poi:

1. importa le ultime partite oppure incolla un PGN;
2. analizza la partita;
3. salva i risultati;
4. controlla il profilo dopo almeno 10-20 partite;
5. ripassa i puzzle indicati come dovuti.

Per spegnere GM-Vibe, torna al terminale del server e premi `Ctrl` + `C`.