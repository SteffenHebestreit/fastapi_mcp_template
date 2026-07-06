"""In-process metrics collector.

A lightweight, dependency-free counter sink. Tools record metrics through
``ToolBase.record_metric(name, value)``; this collector sums them and exposes a
snapshot for the ``/metrics`` endpoint. Not durable across restarts.
"""

import threading
import time
from collections import defaultdict
from typing import Any, Dict


class MetricsCollector:
    """Thread-safe additive counters plus a monotonic start time."""

    def __init__(self):
        self._counters: "defaultdict[str, float]" = defaultdict(float)
        self._lock = threading.Lock()
        self._started_monotonic = time.monotonic()

    def record(self, name: str, value: Any = 1) -> None:
        try:
            amount = float(value)
        except (TypeError, ValueError):
            amount = 1.0
        with self._lock:
            self._counters[name] += amount

    def get(self, name: str) -> float:
        with self._lock:
            return self._counters.get(name, 0.0)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
        uptime = round(time.monotonic() - self._started_monotonic, 3)
        # Round for readability; keep cost values at higher precision.
        rendered = {
            k: (round(v, 6) if "cost" in k else (int(v) if v.is_integer() else round(v, 3)))
            for k, v in sorted(counters.items())
        }
        return {"uptime_seconds": uptime, "counters": rendered}
