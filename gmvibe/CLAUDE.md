# Regole di progetto — GM-Vibe

(da documentazione.txt §11)

- Il motore calcola, il codice misura, Claude spiega. Mai delegare calcolo scacchistico all'LLM.
- Nessuna variante generata dall'LLM viene mostrata senza validazione di legalita'.
- Nessun accesso ad analisi durante partite live (fair play).
- Tutte le soglie e i pesi vivono in config, non nel codice.
- Ogni modulo nuovo ha test; i FEN di test sono in tests/fixtures.
- Una fase alla volta; non iniziare la successiva senza i criteri di accettazione.
- Token, chiavi e password solo in `secrets.env`/keyring, mai nel repo, nei log o nel DB. Non chiedere mai la password di Lichess/Chess.com: solo token.
- Nessun Elo, username o path scritto nel codice: tutto viene dal profilo e da `levels.yaml`.
- L'utente inserisce solo il rating: ogni parametro dipendente dal livello (incluso il modello Maia) passa da `resolve_level`, mai da scelte manuali sparse nel codice.
- Ogni tabella dati utente ha `user_id`; la cache motore e' condivisa.
