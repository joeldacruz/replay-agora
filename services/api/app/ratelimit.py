import time
from collections import defaultdict, deque

from fastapi import HTTPException


class RateLimiter:
    """Janela deslizante em memória (por processo). Em produção multi-instância, usar Redis/proxy."""

    def __init__(self, max_calls: int, window_s: int):
        self.max, self.window = max_calls, window_s
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max:
            raise HTTPException(429, "muitas tentativas; aguarde e tente novamente")
        q.append(now)

    def reset(self) -> None:
        self.hits.clear()
