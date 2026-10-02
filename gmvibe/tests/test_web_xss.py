"""Regressione XSS: i dati non fidati (nomi dei giocatori dal PGN, testo del Maestro,
nomi apertura, messaggi d'errore) vanno HTML-escapati prima di finire in ``innerHTML``.

Il rendering e' lato client (``web/static/app.js``), quindi il test esegue davvero la
funzione ``esc`` con Node — non si limita a cercare stringhe nel sorgente.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "gmvibe" / "web" / "static" / "app.js"


def _extract_esc(source: str) -> str:
    """Estrae la definizione di ``esc`` (e la mappa ``_ESC``) dal sorgente."""
    m = re.search(r"const _ESC = \{.*?\};.*?function esc\(v\) \{.*?\n\}", source, re.S)
    assert m, "definizione di esc() non trovata in app.js"
    return m.group(0)


def test_esc_is_used_for_player_names_and_coach_text():
    """I sink confermati (nome giocatori, testo del coach) devono passare da esc()."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "esc(a.white" in src and "esc(a.black" in src
    assert "esc(report.sintesi_partita)" in src
    assert "esc(cm.spiegazione)" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node non disponibile")
def test_esc_escapes_html_payloads(tmp_path):
    src = APP_JS.read_text(encoding="utf-8")
    script = _extract_esc(src) + (
        "\nconst payloads = ['<img src=x onerror=alert(1)>', '\"quote\"', \"'ap' & <b>\"];\n"
        "console.log(JSON.stringify(payloads.map(esc)));\n"
    )
    f = tmp_path / "esc_check.mjs"
    f.write_text(script, encoding="utf-8")
    out = subprocess.run(["node", str(f)], capture_output=True, text=True, check=True)
    import json

    escaped = json.loads(out.stdout.strip())
    for value in escaped:
        assert "<" not in value and ">" not in value
        assert '"' not in value and "'" not in value
    assert "&lt;img" in escaped[0]
    assert "&amp;" in escaped[2]