import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable


class TTLCache:
    """
    Async TTL cache with:
    - LRU eviction at max_size entries (default 500)
    - Single-flight: concurrent requests for the same key share one loader call
    """

    def __init__(self, max_size: int = 500):
        self._max_size = max_size
        self._entries: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = asyncio.Lock()
        # Per-key in-flight futures to deduplicate concurrent loaders.
        self._inflight: dict[str, asyncio.Future] = {}

    async def get_or_set(
        self,
        key: str,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[object]],
    ):
        now = time.monotonic()

        # Fast path: cache hit (no global lock needed).
        async with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > now:
                self._entries.move_to_end(key)
                return entry[1]

            # If a loader is already running for this key, wait for it.
            if key in self._inflight:
                fut = self._inflight[key]
            else:
                fut = asyncio.get_event_loop().create_future()
                self._inflight[key] = fut
                fut = None  # Signal that we are the designated loader.

        if fut is not None:
            # Another coroutine is loading — wait for the result.
            return await asyncio.shield(fut)

        # We are the designated loader.
        try:
            value = await loader()
            async with self._lock:
                self._entries[key] = (time.monotonic() + ttl_seconds, value)
                self._entries.move_to_end(key)
                # LRU eviction.
                while len(self._entries) > self._max_size:
                    self._entries.popitem(last=False)
                inflight_fut = self._inflight.pop(key, None)
            if inflight_fut and not inflight_fut.done():
                inflight_fut.set_result(value)
            return value
        except Exception as exc:
            async with self._lock:
                inflight_fut = self._inflight.pop(key, None)
            if inflight_fut and not inflight_fut.done():
                inflight_fut.set_exception(exc)
            raise

    async def invalidate_prefix(self, prefix: str):
        async with self._lock:
            stale = [key for key in self._entries if key.startswith(prefix)]
            for key in stale:
                self._entries.pop(key, None)


cache = TTLCache()
