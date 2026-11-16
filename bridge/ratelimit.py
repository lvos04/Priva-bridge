"""Eenvoudige rate limit per sleutel (één proces, geheugen volstaat)."""

from __future__ import annotations

import math
import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, per_minute: int, window: float = 60.0):
        self.per_minute = per_minute
        self.window = window
        self._hits: dict[object, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: object) -> int | None:
        """None als het verzoek mag; anders het aantal seconden voor Retry-After."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return max(1, math.ceil(self.window - (now - hits[0])))
            hits.append(now)
            return None
