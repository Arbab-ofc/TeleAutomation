from collections import defaultdict, deque
from time import monotonic


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] >= window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False, max(1, int(window_seconds - (now - hits[0])))
        hits.append(now)
        return True, 0
