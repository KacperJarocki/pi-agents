import asyncio
import time
from collections.abc import Awaitable, Callable


class TTLCache:
    def __init__(self):
        self._entries: dict[str, tuple[float, object]] = {}
        self._lock = asyncio.Lock()

    async def get_or_set(
        self,
        key: str,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[object]],
    ):
        now = time.monotonic()
        async with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > now:
                return entry[1]

        value = await loader()

        async with self._lock:
            self._entries[key] = (time.monotonic() + ttl_seconds, value)
        return value

    async def invalidate_prefix(self, prefix: str):
        async with self._lock:
            stale = [key for key in self._entries if key.startswith(prefix)]
            for key in stale:
                self._entries.pop(key, None)


cache = TTLCache()
