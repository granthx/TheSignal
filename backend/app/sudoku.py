"""Sudoku engine: seeded generation with a guaranteed unique solution.

Difficulty is controlled by the number of given clues (easy 40, medium 32, hard ~26). That is an honest,
simple proxy; it is not a logical-technique rating. Puzzles are generated with 180-degree rotational
symmetry (like printed newspaper puzzles), and the same date + difficulty always yields the same puzzle.
"""
from __future__ import annotations

import hashlib
import random
import secrets
from functools import lru_cache

ALL = 0x1FF
ROW = [i // 9 for i in range(81)]
COL = [i % 9 for i in range(81)]
BOX = [(ROW[i] // 3) * 3 + COL[i] // 3 for i in range(81)]
TARGET_CLUES = {"easy": 40, "medium": 32, "hard": 26}


def _solve(grid: list[int], limit: int, rng: random.Random | None = None) -> list[tuple[int, ...]]:
    """Backtracking solver (bitmask candidates, most-constrained-cell first). Returns up to ``limit``
    solutions; an invalid grid returns []. With ``rng`` candidate order is shuffled (for generation)."""
    cells = list(grid)
    rows, cols, boxes = [0] * 9, [0] * 9, [0] * 9
    empties: list[int] = []
    for i, v in enumerate(cells):
        if v:
            bit = 1 << (v - 1)
            if rows[ROW[i]] & bit or cols[COL[i]] & bit or boxes[BOX[i]] & bit:
                return []
            rows[ROW[i]] |= bit
            cols[COL[i]] |= bit
            boxes[BOX[i]] |= bit
        else:
            empties.append(i)

    solutions: list[tuple[int, ...]] = []

    def search() -> None:
        best, best_mask, best_n = -1, 0, 10
        for i in empties:
            if cells[i]:
                continue
            mask = ALL & ~(rows[ROW[i]] | cols[COL[i]] | boxes[BOX[i]])
            n = mask.bit_count()
            if n == 0:
                return
            if n < best_n:
                best, best_mask, best_n = i, mask, n
                if n == 1:
                    break
        if best == -1:
            solutions.append(tuple(cells))
            return
        digits = [d for d in range(1, 10) if best_mask >> (d - 1) & 1]
        if rng is not None:
            rng.shuffle(digits)
        r, c, b = ROW[best], COL[best], BOX[best]
        for d in digits:
            bit = 1 << (d - 1)
            cells[best] = d
            rows[r] |= bit
            cols[c] |= bit
            boxes[b] |= bit
            search()
            rows[r] &= ~bit
            cols[c] &= ~bit
            boxes[b] &= ~bit
            cells[best] = 0
            if len(solutions) >= limit:
                return

    search()
    return solutions


def count_solutions(grid: list[int], limit: int = 2) -> int:
    return len(_solve(grid, limit))


def solve(grid: list[int]) -> list[int] | None:
    found = _solve(grid, 1)
    return list(found[0]) if found else None


def seed_from(*parts: object) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def generate(difficulty: str, seed: int) -> dict:
    if difficulty not in TARGET_CLUES:
        raise ValueError(f"difficulty must be one of {sorted(TARGET_CLUES)}")
    rng = random.Random(seed)
    solution = list(_solve([0] * 81, 1, rng)[0])
    puzzle = list(solution)
    target = TARGET_CLUES[difficulty]

    order = list(range(41))  # cell i and its 180-degree partner 80-i are removed together
    rng.shuffle(order)
    for i in order:
        if sum(1 for v in puzzle if v) <= target:
            break
        j = 80 - i
        kept = (puzzle[i], puzzle[j])
        puzzle[i] = puzzle[j] = 0
        if count_solutions(puzzle, 2) != 1:
            puzzle[i], puzzle[j] = kept

    return {
        "difficulty": difficulty,
        "puzzle": "".join(map(str, puzzle)),
        "solution": "".join(map(str, solution)),
        "clues": sum(1 for v in puzzle if v),
    }


@lru_cache(maxsize=64)
def daily(date: str, difficulty: str) -> dict:
    """The same puzzle for everyone on a given date, like the one in a printed newspaper."""
    return {**generate(difficulty, seed_from("the-signal-sudoku", date, difficulty)), "date": date, "daily": True}


def random_puzzle(difficulty: str) -> dict:
    return {**generate(difficulty, secrets.randbits(64)), "date": None, "daily": False}
