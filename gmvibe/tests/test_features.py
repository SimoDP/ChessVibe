"""Test delle feature posizionali su FEN costruiti a mano (documentazione.txt §5.F).

Almeno 3 casi per feature, come richiesto dai criteri di accettazione della FASE 3.
"""

from __future__ import annotations

import chess

from gmvibe.analysis import features as F


def b(fen: str) -> chess.Board:
    return chess.Board(fen)


# --------------------------------------------------------------------------- #
# Pedoni isolati
# --------------------------------------------------------------------------- #
def test_isolated_pawns_three_cases():
    # d4 isolato: nessun pedone amico su c o e
    assert chess.D4 in F.isolated_pawns(b("8/8/8/8/3P4/8/8/4K2k w - - 0 1"), chess.WHITE)
    # c2+d2 non isolati (adiacenti)
    iso = F.isolated_pawns(b("8/8/8/8/8/8/2PP4/4K2k w - - 0 1"), chess.WHITE)
    assert chess.C2 not in iso and chess.D2 not in iso
    # a2 isolato, e2 isolato (distanti)
    iso2 = F.isolated_pawns(b("8/8/8/8/8/8/P3P3/4K2k w - - 0 1"), chess.WHITE)
    assert set(iso2) == {chess.A2, chess.E2}


# --------------------------------------------------------------------------- #
# Pedoni doppiati
# --------------------------------------------------------------------------- #
def test_doubled_pawns_three_cases():
    # due pedoni bianchi su c
    dd = F.doubled_pawns(b("8/8/8/8/2P5/2P5/8/4K2k w - - 0 1"), chess.WHITE)
    assert set(dd) == {chess.C4, chess.C3}
    # due pedoni neri su f
    ddn = F.doubled_pawns(b("5p2/5p2/8/8/8/8/8/4K2k w - - 0 1"), chess.BLACK)
    assert len(ddn) == 2
    # nessun pedone doppiato
    assert F.doubled_pawns(b("8/8/8/8/8/8/PP6/4K2k w - - 0 1"), chess.WHITE) == []


# --------------------------------------------------------------------------- #
# Pedoni passati
# --------------------------------------------------------------------------- #
def test_passed_pawns_three_cases():
    # e5 passato (nessun pedone nero davanti su d/e/f)
    assert chess.E5 in F.passed_pawns(b("8/8/8/4P3/8/8/8/4K2k w - - 0 1"), chess.WHITE)
    # e4 NON passato: pedone nero in e7 blocca
    assert chess.E4 not in F.passed_pawns(b("4r1k1/4p3/8/8/4P3/8/8/4K3 w - - 0 1".replace("r", "p")), chess.WHITE)
    # nero d4 passato (nessun pedone bianco davanti)
    assert chess.D4 in F.passed_pawns(b("8/8/8/8/3p4/8/8/4K2k b - - 0 1"), chess.BLACK)


# --------------------------------------------------------------------------- #
# Avamposti
# --------------------------------------------------------------------------- #
def test_outposts_three_cases():
    # cavallo bianco in d5 (5a traversa), non attaccabile da pedoni neri
    assert chess.D5 in F.outposts(b("8/8/8/3N4/8/8/8/4K2k w - - 0 1"), chess.WHITE)
    # cavallo bianco in d5 attaccabile da pedone nero in c6 -> non avamposto
    assert chess.D5 not in F.outposts(b("8/8/2p5/3N4/8/8/8/4K2k w - - 0 1"), chess.WHITE)
    # cavallo bianco in d2 (2a traversa) -> non avamposto (troppo arretrato)
    assert chess.D2 not in F.outposts(b("8/8/8/8/8/8/3N4/4K2k w - - 0 1"), chess.WHITE)


# --------------------------------------------------------------------------- #
# Colonne aperte / semiaperte
# --------------------------------------------------------------------------- #
def test_open_files_three_cases():
    # colonna e completamente aperta
    assert "e" in F.open_files(b("8/8/8/8/8/8/P7/4K2k w - - 0 1"))
    # colonne a..h tutte con pedoni -> nessuna aperta
    assert F.open_files(b("pppppppp/8/8/8/8/8/8/PPPPPPPP w - - 0 1")) == []
    # solo la colonna d aperta
    assert F.open_files(b("ppp1pppp/8/8/8/8/8/8/PPP1PPPP w - - 0 1")) == ["d"]


def test_semi_open_files():
    # semiaperta per il bianco: nessun pedone bianco su d, ma un pedone nero su d
    semi = F.semi_open_files(b("pppppppp/8/8/8/8/8/PPP1PPPP/8 w - - 0 1"), chess.WHITE)
    assert "d" in semi


# --------------------------------------------------------------------------- #
# Sicurezza del Re
# --------------------------------------------------------------------------- #
def test_king_shield_three_cases():
    # tre pedoni davanti al Re in g1
    assert F.king_pawn_shield(b("8/8/8/8/8/8/5PPP/6K1 w - - 0 1"), chess.WHITE) == 3
    # un solo pedone al centro
    assert F.king_pawn_shield(b("8/8/8/8/8/8/4P3/4K3 w - - 0 1"), chess.WHITE) == 1
    # nessun pedone davanti al Re
    assert F.king_pawn_shield(b("8/8/8/8/8/8/8/4K3 w - - 0 1"), chess.WHITE) == 0


def test_castled():
    assert F.castled(b("8/8/8/8/8/8/8/6K1 w - - 0 1"), chess.WHITE) is True
    assert F.castled(b("8/8/8/8/8/8/8/2K5 w - - 0 1"), chess.WHITE) is True
    assert F.castled(b("8/8/8/8/8/8/8/4K3 w - - 0 1"), chess.WHITE) is False


# --------------------------------------------------------------------------- #
# Mobilità
# --------------------------------------------------------------------------- #
def test_mobility_three_cases():
    # posizione iniziale: 20 mosse per il bianco
    assert F.side_mobility(b("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"), chess.WHITE) == 20
    # Re solo in g1: 5 case raggiungibili (f1,f2,g2,h1,h2)
    assert F.side_mobility(b("8/8/8/8/8/8/8/6K1 w - - 0 1"), chess.WHITE) == 5
    # donna al centro: molte mosse
    assert F.side_mobility(b("8/8/8/8/3Q4/8/8/4K2k w - - 0 1"), chess.WHITE) > 20


# --------------------------------------------------------------------------- #
# Alfieri
# --------------------------------------------------------------------------- #
def test_bishop_pair_three_cases():
    assert F.has_bishop_pair(b("8/8/8/8/8/8/8/2BBK2k w - - 0 1"), chess.WHITE) is True
    assert F.has_bishop_pair(b("8/8/8/8/8/8/8/2B1K2k w - - 0 1"), chess.WHITE) is False
    assert F.has_bishop_pair(b("8/8/8/8/8/8/8/2BBK2k w - - 0 1"), chess.BLACK) is False


def test_opposite_colored_bishops():
    # Bc1 (scura) vs Bc8 (chiara) -> opposti
    assert F.opposite_colored_bishops(b("2b1k3/8/8/8/8/8/8/2B1K3 w - - 0 1")) is True
    # Bc1 (scura) vs Bf8 (scura) -> stesso colore
    assert F.opposite_colored_bishops(b("5b2/8/8/8/8/8/8/2B1K3 w - - 0 1")) is False
    # due alfieri bianchi -> non applicabile
    assert F.opposite_colored_bishops(b("2b1k3/8/8/8/8/8/8/2BBK3 w - - 0 1")) is False


def test_rooks_on_seventh():
    assert F.rooks_on_seventh(b("8/4R3/8/8/8/8/8/4K2k w - - 0 1"), chess.WHITE) == 1
    assert F.rooks_on_seventh(b("8/8/8/8/8/8/8/4K2k w - - 0 1"), chess.WHITE) == 0
    assert F.rooks_on_seventh(b("4r3/8/8/8/8/8/8/4K2k w - - 0 1"), chess.BLACK) == 0


# --------------------------------------------------------------------------- #
# Fact sheet e delta
# --------------------------------------------------------------------------- #
def test_compute_features_shape():
    feats = F.compute_features(b("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"))
    assert feats.material_balance == 0
    assert feats.white.mobility == 20
    assert feats.white.castled is False


def test_feature_delta_reflects_move():
    before = F.compute_features(b("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"))
    after = F.compute_features(b("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"))
    delta = F.feature_delta(before, after)
    assert "white_mobility" in delta
    assert "material_balance" in delta
    assert delta["material_balance"] == 0
