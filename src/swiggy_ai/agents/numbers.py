"""Check that every number in an answer really comes from the result.

A model that "remembers" a figure, or rounds it its own way, is a silent source of wrong answers.
So after the narrator writes, we pull every number out of the text and look for it in the rows.
"""
import re
from typing import Any

NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")


def parse_numbers(text: str) -> list[tuple[str, float, bool, int]]:
    """(as written, value, is_percent, decimals written) for every number in the text."""
    out = []
    for m in NUMBER.finditer(text):
        raw = m.group(0)
        pct = raw.endswith("%")
        body = raw.rstrip("%").replace(",", "")
        try:
            value = float(body)
        except ValueError:
            continue
        decimals = len(body.split(".")[1]) if "." in body else 0
        out.append((raw, value, pct, decimals))
    return out


def allowed_numbers(columns: list[str], rows: list[list[Any]],
                    context_text: str = "") -> set[float]:
    """Every figure the answer may use: cell values, row counts, ranks, and digits that appear in
    the question or in text cells (dates, ids, names)."""
    allowed: set[float] = {float(i) for i in range(0, len(rows) + 2)}
    allowed.add(float(len(columns)))
    for row in rows:
        for cell in row:
            if isinstance(cell, bool):
                continue
            if isinstance(cell, int | float):
                allowed.add(float(cell))
            elif isinstance(cell, str):
                for _, v, _, _ in parse_numbers(cell):
                    allowed.add(v)
    for _, v, _, _ in parse_numbers(context_text):
        allowed.add(v)
    return allowed


def _supported(value: float, pct: bool, decimals: int, allowed: set[float]) -> bool:
    tol = 0.5 * 10 ** (-decimals) + 1e-9  # a figure rounded to d decimals may be off by half a unit
    for a in allowed:
        if abs(value - a) <= tol:
            return True
        if pct and abs(value / 100 - a) <= tol / 100:  # "4.02%" for a stored ratio of 0.0402
            return True
    return False


def unsupported_numbers(answer: str, allowed: set[float]) -> list[str]:
    return [raw for raw, v, pct, d in parse_numbers(answer) if not _supported(v, pct, d, allowed)]
