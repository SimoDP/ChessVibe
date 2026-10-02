"""Feature posizionali deterministiche (documentazione.txt §5.F).

Calcolate con ``python-chess``, MAI dedotte da Claude. Ogni numero di questo modulo e'
un fatto verificabile sulla posizione. Le feature servono a: (a) dare a Claude un "fact
sheet" per spiegare la posizione; (b) calcolare il delta prima/dopo la mossa.

Non si usa il comando ``eval`` di Stockfish (l'output cambia tra versioni): tutto qui e'
calcolato da noi.
"""

from __future__ import annotations

import chess

from ..models import PositionFeatures, SideFeatures

VALUES = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}

_CENTER = (chess.D4, chess.E4, chess.D5, chess.E5)


# --------------------------------------------------------------------------- #
# Struttura pedonale
# --------------------------------------------------------------------------- #
def _pawns(board: chess.Board, color: chess.Color) -> list[int]:
    return list(board.pieces(chess.PAWN, color))


def _adjacent_files(file_idx: int):
    return [f for f in (file_idx - 1, file_idx + 1) if 0 <= f <= 7]


def isolated_pawns(board: chess.Board, color: chess.Color) -> list[int]:
    """Pedoni senza pedoni amici su file adiacenti."""
    own = _pawns(board, color)
    files_with_pawns = {chess.square_file(s) for s in own}
    return [s for s in own if not any(f in files_with_pawns for f in _adjacent_files(chess.square_file(s)))]


def doubled_pawns(board: chess.Board, color: chess.Color) -> list[int]:
    """Pedoni su una colonna dove c'e' piu' di un pedone amico."""
    own = _pawns(board, color)
    counts: dict[int, int] = {}
    for s in own:
        counts[chess.square_file(s)] = counts.get(chess.square_file(s), 0) + 1
    return [s for s in own if counts[chess.square_file(s)] > 1]


def passed_pawns(board: chess.Board, color: chess.Color) -> list[int]:
    """Pedoni senza pedoni avversari sulla stessa colonna o adiacenti davanti a loro."""
    own = _pawns(board, color)
    enemy = _pawns(board, not color)
    result = []
    for s in own:
        f, r = chess.square_file(s), chess.square_rank(s)
        blocked = False
        for e in enemy:
            ef, er = chess.square_file(e), chess.square_rank(e)
            if ef in (f, *_adjacent_files(f)):
                ahead = er > r if color == chess.WHITE else er < r
                if ahead:
                    blocked = True
                    break
        if not blocked:
            result.append(s)
    return result


def backward_pawns(board: chess.Board, color: chess.Color) -> list[int]:
    """Pedoni non isolati che non possono essere difesi da pedoni amici adiacenti e la
    cui casa di avanzamento e' controllata da un pedone avversario."""
    own = _pawns(board, color)
    result = []
    for s in own:
        f, r = chess.square_file(s), chess.square_rank(s)
        # file adiacenti senza pedoni amici che possano difenderlo (stessa traversa o dietro)
        friends = [
            p for p in own
            if chess.square_file(p) in _adjacent_files(f) and p != s
        ]
        can_defend = False
        for p in friends:
            pr = chess.square_rank(p)
            behind_or_level = pr <= r if color == chess.WHITE else pr >= r
            if behind_or_level:
                can_defend = True
                break
        if can_defend:
            continue
        # casa di avanzamento attaccata da un pedone avversario?
        adv_rank = r + 1 if color == chess.WHITE else r - 1
        if not (0 <= adv_rank <= 7):
            continue
        adv_sq = chess.square(f, adv_rank)
        if _attacked_by_pawn(board, adv_sq, not color):
            result.append(s)
    return result


def _attacked_by_pawn(board: chess.Board, square: int, by: chess.Color) -> bool:
    """Vero se ``square`` e' attaccato da un pedone di colore ``by``."""
    f, r = chess.square_file(square), chess.square_rank(square)
    pawn_rank = r - 1 if by == chess.WHITE else r + 1
    if not (0 <= pawn_rank <= 7):
        return False
    for pf in _adjacent_files(f):
        if board.piece_at(chess.square(pf, pawn_rank)) == chess.Piece(chess.PAWN, by):
            return True
    return False


def is_iqp(board: chess.Board, color: chess.Color) -> bool:
    """Pedone di donna isolato: donna in d4 (bianco) / d5 (nero) senza pedoni adiacenti."""
    sq = chess.D4 if color == chess.WHITE else chess.D5
    if board.piece_at(sq) != chess.Piece(chess.PAWN, color):
        return False
    return sq in isolated_pawns(board, color)


def has_hanging_pawns(board: chess.Board, color: chess.Color) -> bool:
    """Pedoni sospesi: coppia c+d sulla 4a (bianco) / 5a (nero), senza pedoni su b o e."""
    squares = (chess.C4, chess.D4) if color == chess.WHITE else (chess.C5, chess.D5)
    if not all(board.piece_at(sq) == chess.Piece(chess.PAWN, color) for sq in squares):
        return False
    own = board.pieces(chess.PAWN, color)
    # niente pedoni amici sulle colonne adiacenti (b ed e)
    if own & chess.BB_FILES[1] or own & chess.BB_FILES[4]:  # colonne b ed e
        return False
    return True


# --------------------------------------------------------------------------- #
# Colonne
# --------------------------------------------------------------------------- #
def _file_has_pawn(board: chess.Board, file_idx: int) -> bool:
    return bool(
        (board.pieces(chess.PAWN, chess.WHITE) | board.pieces(chess.PAWN, chess.BLACK))
        & chess.BB_FILES[file_idx]
    )


def open_files(board: chess.Board) -> list[str]:
    """Colonne senza pedoni di nessun colore."""
    return [chess.FILE_NAMES[f] for f in range(8) if not _file_has_pawn(board, f)]


def semi_open_files(board: chess.Board, color: chess.Color) -> list[str]:
    """Colonne semiaperte dal punto di vista di ``color``: senza pedoni propri ma con
    almeno un pedone avversario."""
    result = []
    for f in range(8):
        own = board.pieces(chess.PAWN, color) & chess.BB_FILES[f]
        enemy = board.pieces(chess.PAWN, not color) & chess.BB_FILES[f]
        if not own and enemy:
            result.append(chess.FILE_NAMES[f])
    return result


# --------------------------------------------------------------------------- #
# Avamposti, alfieri, torri
# --------------------------------------------------------------------------- #
def outposts(board: chess.Board, color: chess.Color) -> list[int]:
    """Case d'avamposto per i cavalli: occupate da un cavallo su traverse 4-6 (relativa),
    non attaccabili da pedoni avversari."""
    result = []
    for sq in board.pieces(chess.KNIGHT, color):
        r = chess.square_rank(sq)
        if color == chess.WHITE:
            if not (3 <= r <= 5):  # 4a-6a traversa
                continue
        else:
            if not (2 <= r <= 4):  # 5a-3a traversa (dal punto di vista nero)
                continue
        if not _attacked_by_pawn(board, sq, not color):
            result.append(sq)
    return result


def _square_color(square: int) -> int:
    """Colore di casa come parita' (0 = scura, 1 = chiara). python-chess non espone
    ``square_color`` in questa build."""
    return (chess.square_file(square) + chess.square_rank(square)) & 1


def has_bishop_pair(board: chess.Board, color: chess.Color) -> bool:
    return len(board.pieces(chess.BISHOP, color)) >= 2


def opposite_colored_bishops(board: chess.Board) -> bool:
    """Un solo alfiere per parte, su colori di casa opposti."""
    wb = board.pieces(chess.BISHOP, chess.WHITE)
    bb = board.pieces(chess.BISHOP, chess.BLACK)
    if len(wb) != 1 or len(bb) != 1:
        return False
    return _square_color(next(iter(wb))) != _square_color(next(iter(bb)))


def bad_bishop(board: chess.Board, color: chess.Color) -> bool:
    """Alfiere "cattivo": pochi movimenti e bloccato dai propri pedoni su case del suo colore."""
    bishops = board.pieces(chess.BISHOP, color)
    if not bishops:
        return False
    own_pawns = board.pieces(chess.PAWN, color)
    for b in bishops:
        bcolor = _square_color(b)
        same_color_pawns = sum(1 for p in own_pawns if _square_color(p) == bcolor)
        moves = _piece_mobility(board, b, color)
        if same_color_pawns >= 4 and moves <= 3:
            return True
    return False


def rooks_on_seventh(board: chess.Board, color: chess.Color) -> int:
    seventh = 6 if color == chess.WHITE else 1
    return sum(1 for sq in board.pieces(chess.ROOK, color) if chess.square_rank(sq) == seventh)


# --------------------------------------------------------------------------- #
# Re, mobilità, centro
# --------------------------------------------------------------------------- #
def castled(board: chess.Board, color: chess.Color) -> bool:
    king = board.king(color)
    if king is None:
        return False
    return king in ((chess.G1, chess.C1) if color == chess.WHITE else (chess.G8, chess.C8))


def king_pawn_shield(board: chess.Board, color: chess.Color) -> int:
    """Numero di pedoni amici davanti al Re (3 colonne, fino a 2 traverse avanti)."""
    king = board.king(color)
    if king is None:
        return 0
    kf, kr = chess.square_file(king), chess.square_rank(king)
    count = 0
    for f in [kf - 1, kf, kf + 1]:
        if not (0 <= f <= 7):
            continue
        for dr in (1, 2):
            r = kr + dr if color == chess.WHITE else kr - dr
            if 0 <= r <= 7 and board.piece_at(chess.square(f, r)) == chess.Piece(chess.PAWN, color):
                count += 1
                break
    return count


def _piece_mobility(board: chess.Board, square: int, color: chess.Color) -> int:
    """Numero di mosse legali del pezzo su ``square`` (per il lato ``color``)."""
    b = board.copy(stack=False)
    b.turn = color
    b.clear_stack()
    return sum(1 for m in b.legal_moves if m.from_square == square)


def side_mobility(board: chess.Board, color: chess.Color) -> int:
    """Mobilità totale del lato: numero di mosse legali disponibili."""
    b = board.copy(stack=False)
    b.turn = color
    b.clear_stack()
    return b.legal_moves.count()


def center_control(board: chess.Board, color: chess.Color) -> int:
    """Numero di case centrali (d4,e4,d5,e5) attaccate da ``color``."""
    b = board.copy(stack=False)
    b.turn = color
    b.clear_stack()
    return sum(1 for sq in _CENTER if b.attacks(sq) & b.occupied_co[color])


# --------------------------------------------------------------------------- #
# Fact sheet completo e delta
# --------------------------------------------------------------------------- #
def _material_balance(board: chess.Board) -> int:
    total = 0
    for pt, val in VALUES.items():
        total += len(board.pieces(pt, chess.WHITE)) * val
        total -= len(board.pieces(pt, chess.BLACK)) * val
    return total


def _side_features(board: chess.Board, color: chess.Color) -> SideFeatures:
    return SideFeatures(
        has_bishop_pair=has_bishop_pair(board, color),
        isolated_pawns=[chess.square_name(s) for s in isolated_pawns(board, color)],
        doubled_pawns=[chess.square_name(s) for s in doubled_pawns(board, color)],
        backward_pawns=[chess.square_name(s) for s in backward_pawns(board, color)],
        passed_pawns=[chess.square_name(s) for s in passed_pawns(board, color)],
        iqp=is_iqp(board, color),
        hanging_pawns=has_hanging_pawns(board, color),
        outposts=[chess.square_name(s) for s in outposts(board, color)],
        bad_bishop=bad_bishop(board, color),
        rooks_on_seventh=rooks_on_seventh(board, color),
        mobility=side_mobility(board, color),
        king_pawn_shield=king_pawn_shield(board, color),
        castled=castled(board, color),
    )


def compute_features(board: chess.Board) -> PositionFeatures:
    """Fact sheet strutturato per entrambi i lati (§5.F)."""
    return PositionFeatures(
        material_balance=_material_balance(board),
        opposite_colored_bishops=opposite_colored_bishops(board),
        open_files=open_files(board),
        semi_open_files=semi_open_files(board, chess.WHITE) + semi_open_files(board, chess.BLACK),
        center_control=center_control(board, chess.WHITE) - center_control(board, chess.BLACK),
        white=_side_features(board, chess.WHITE),
        black=_side_features(board, chess.BLACK),
    )


def feature_delta(before: PositionFeatures, after: PositionFeatures) -> dict[str, float]:
    """Differenze numeriche tra posizione prima e dopo la mossa (§5.F).

    Valori dal punto di vista del Bianco (positivo = migliora per il Bianco).
    """
    delta: dict[str, float] = {
        "material_balance": after.material_balance - before.material_balance,
        "center_control": after.center_control - before.center_control,
        "white_mobility": after.white.mobility - before.white.mobility,
        "black_mobility": after.black.mobility - before.black.mobility,
        "white_king_shield": after.white.king_pawn_shield - before.white.king_pawn_shield,
        "black_king_shield": after.black.king_pawn_shield - before.black.king_pawn_shield,
        "white_pawn_weaknesses": (
            len(after.white.isolated_pawns) + len(after.white.doubled_pawns)
            + len(after.white.backward_pawns)
        ) - (
            len(before.white.isolated_pawns) + len(before.white.doubled_pawns)
            + len(before.white.backward_pawns)
        ),
        "black_pawn_weaknesses": (
            len(after.black.isolated_pawns) + len(after.black.doubled_pawns)
            + len(after.black.backward_pawns)
        ) - (
            len(before.black.isolated_pawns) + len(before.black.doubled_pawns)
            + len(before.black.backward_pawns)
        ),
    }
    return delta
