# Prompt — Analisi errore

Posizione: {{fen_before}} -> {{fen_after}}
Mossa giocata: {{san}} ({{uci}})
Etichetta errore: {{label}}  (ok / imprecisione / errore / svarione)
Eval prima: {{score_cp}} cp, win% {{winpct_before}}
Eval dopo: {{score_cp}} cp, win% {{winpct_after}}
Calo win%: {{winpct_loss}}

Feature posizione (prima):
{{features_before}}

Contesto:
- Fase: {{phase}}
- Motore: Stockfish depth={{depth}}, multipv={{multipv}}
- Tempismo: {{clock_seconds}}s

Istruzioni:
1. Spiega in 2-4 frasi perche' la mossa e' un'{{label}}.
2. Indica la migliore alternativa (SAN + eval).
3. Descrivi il concetto scacchistico violato (tempo, tattica, struttura, finali).
4. Suggerisci 1 esercizio concettuale per non ripetere l'errore.

Mai inventare variazioni: usa solo mosse verificabili legali.
