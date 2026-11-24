from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import notes


class NoteCache:
    """Note text kept between page loads. A path is re-checked (one stat) at most every ttl seconds and re-read
    only when its modified time or size changed; missing files are remembered as None. The hub clears it around
    its own writes, so ttl only delays edits made outside the hub (Obsidian, Explorer)."""

    def __init__(self, ttl: float = 30.0, clock=time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self._items: dict[str, tuple[float, tuple[int, int] | None, str | None]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(path: str | Path) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def read(self, path: str | Path | None) -> str | None:
        if not path:
            return None
        key = self._key(path)
        now = self.clock()
        with self._lock:
            item = self._items.get(key)
        if item is not None and now - item[0] < self.ttl:
            return item[2]
        try:
            info = notes.lp(path).stat()
            signature = (info.st_mtime_ns, info.st_size)
        except OSError:
            signature = None
        if signature is None:
            text = None
        elif item is not None and item[1] == signature:
            text = item[2]
        else:
            try:
                text = notes.read_text(path)[0]
            except (OSError, ValueError):
                text = None
        with self._lock:
            self._items[key] = (now, signature, text)
        return text

    def prefetch(self, paths, workers: int = 16) -> None:
        """Re-check expired paths in parallel: on a network drive each check is mostly waiting."""
        now = self.clock()
        with self._lock:
            stale = [
                path for path in {str(path) for path in paths if path}
                if now - self._items.get(self._key(path), (float("-inf"),))[0] >= self.ttl
            ]
        if len(stale) < 2:
            for path in stale:
                self.read(path)
            return
        with ThreadPoolExecutor(max_workers=min(workers, len(stale))) as pool:
            list(pool.map(self.read, stale))

    def forget(self, path: str | Path | None) -> None:
        if not path:
            return
        with self._lock:
            self._items.pop(self._key(path), None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
