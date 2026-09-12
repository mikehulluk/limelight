from __future__ import annotations

import logging
from time import perf_counter
from typing import Protocol, Sequence


class TimingProbeLike(Protocol):
    def start(self) -> float: ...

    def log(self, label: str, start_time: float, fields: Sequence[tuple[str, object]] = ()) -> None: ...


_timing_logger = logging.getLogger("limelight.largeseries.timing")


class TimingProbe:
    """Local, dependency-free duplicate of limelight.app.TimingProbe's shape."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def start(self) -> float:
        return perf_counter()

    def log(self, label: str, start_time: float, fields: Sequence[tuple[str, object]] = ()) -> None:
        if not self.enabled:
            return

        elapsed_ms = (perf_counter() - start_time) * 1000.0
        field_text = " ".join(f"{name}={value}" for name, value in fields)
        suffix = f" {field_text}" if field_text else ""
        _timing_logger.info("%s elapsed_ms=%.3f%s", label, elapsed_ms, suffix)
