"""Run traces (one JSONL file per run) and token/cost accounting."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# USD per million tokens: (input, output, cache write, cache read).
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, usage) -> None:
        for name in asdict(self):
            setattr(self, name, getattr(self, name) + (getattr(usage, name, None) or 0))

    def cost_usd(self, model: str) -> float | None:
        """Estimated cost, or None when the model's prices are not in the table."""
        if model not in PRICES:
            return None
        price_in, price_out, price_write, price_read = PRICES[model]
        total = (
            self.input_tokens * price_in
            + self.output_tokens * price_out
            + self.cache_creation_input_tokens * price_write
            + self.cache_read_input_tokens * price_read
        )
        return round(total / 1_000_000, 4)


class Tracer:
    """Appends one JSON event per line, so a run can be audited or replayed step by step."""

    def __init__(self, directory: str | Path = "runs"):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = directory / f"{stamp}-{uuid.uuid4().hex[:8]}.jsonl"

    def log(self, event: str, **fields) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
