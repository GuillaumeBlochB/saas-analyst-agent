"""Deterministic checks applied to a report's text. No LLM judge: a failure is always explainable."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

_NUMBER = re.compile(r"(?<![\w.])\$?(\d{1,3}(?:[,   ]\d{3})+|\d+)(?:[.,](\d+))?\s?([kKmM])?(?![\w])")
_SCALE = {"k": 1_000, "m": 1_000_000}


@dataclass(frozen=True)
class Check:
    name: str
    passes: Callable[[str], bool]


def numbers_in(text: str) -> list[float]:
    """Numbers written in a text: 284,310 / 284 310 / 284.3k / 0.28M / 14.8."""
    values = []
    for whole, decimals, suffix in _NUMBER.findall(text):
        value = float(re.sub(r"\D", "", whole) + (f".{decimals}" if decimals else ""))
        values.append(value * _SCALE.get(suffix.lower(), 1))
    return values


def mentions(name: str, *patterns: str) -> Check:
    """Passes when at least one of the regex patterns appears (case-insensitive)."""
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    return Check(name, lambda text: any(pattern.search(text) for pattern in compiled))


def number_near(name: str, target: float, *, rel_tol: float = 0.0, abs_tol: float = 0.0) -> Check:
    """Passes when the text contains a number within tolerance of the ground-truth value."""
    tolerance = max(abs(target) * rel_tol, abs_tol)
    return Check(name, lambda text: any(abs(value - target) <= tolerance for value in numbers_in(text)))
