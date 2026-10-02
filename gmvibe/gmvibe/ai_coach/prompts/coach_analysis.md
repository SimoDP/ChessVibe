Analizza questa partita e restituisci il report del Maestro virtuale secondo lo
schema JSON fornito.

ISTRUZIONI DI COMPILAZIONE
- `sintesi_partita`: 3-4 frasi su com'è andata davvero e dove si è decisa.
- `apertura`: valutazione dell'apertura, deviazione dalla teoria (usa solo i dati
  `apertura` del pacchetto), cosa ripassare. Se l'apertura non è nel pacchetto,
  lascia i campi vuoti senza inventare.
- `momenti`: uno per ogni momento critico fornito, con lo stesso `id`. Per ciascuno:
  `titolo` breve e concreto; `spiegazione` (cosa volevi fare / cosa ti è sfuggito /
  perché la mossa migliore funziona); `idea_del_motore` (il piano nascosto dietro la
  mossa del motore); `regola_di_pensiero` (la regola che avrebbe evitato l'errore,
  per gli errori), `regola_di_pensiero` vuota per i momenti positivi.
  `varianti_citate` solo se servono davvero: ogni variante usa un `fen_partenza`
  presente tra i `fen_prima` dei momenti e mosse legali (max 2 semimosse oltre le
  linee del motore).
- `gestione_tempo`: commenta i dati `tempo` e i `clock_secondi`/`tempo_speso_secondi`
  dei momenti. Se il pacchetto non ha dati di tempo, dillo.
- `fase_piu_debole`: apertura | mediogioco | finale, coerente con `accuratezza`.
- `lezione_finale`: la singola lezione più utile, in 1-2 frasi.
- `da_allenare`: 3 azioni concrete e specifiche per questo giocatore.

Ricorda: non calcolare nulla, usa solo i dati forniti.

PACCHETTO DATI (JSON):
{{contesto_json}}
