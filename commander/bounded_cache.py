"""Small process-local LRU caches with deterministic memory budgets."""

from __future__ import annotations

from collections import OrderedDict
import json
import os
import threading
from typing import Any, Callable, Iterator, MutableMapping


def _json_size(value: Any) -> int:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        return len(repr(value).encode("utf-8", errors="replace"))


def env_positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return max(1, int(default))


def process_rss_bytes() -> int | None:
    """Return current RSS without requiring psutil."""
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            resident_pages = int(handle.read().split()[1])
        return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        try:
            import psutil  # type: ignore

            return int(psutil.Process(os.getpid()).memory_info().rss)
        except Exception:
            return None


class BoundedLRUCache(MutableMapping[str, Any]):
    """Thread-safe LRU bounded by both entry count and estimated JSON bytes."""

    def __init__(
        self,
        *,
        max_items: int,
        max_bytes: int,
        size_of: Callable[[Any], int] | None = None,
    ) -> None:
        self.max_items = max(1, int(max_items))
        self.max_bytes = max(1, int(max_bytes))
        self._size_of = size_of or _json_size
        self._items: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._bytes = 0
        self._evictions = 0
        self._lock = threading.RLock()

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            value, size = self._items[key]
            self._items.move_to_end(key)
            return value

    def __setitem__(self, key: str, value: Any) -> None:
        normalized = str(key)
        size = max(1, int(self._size_of(value)))
        with self._lock:
            previous = self._items.pop(normalized, None)
            if previous is not None:
                self._bytes -= previous[1]
            self._items[normalized] = (value, size)
            self._bytes += size
            self._evict()

    def __delitem__(self, key: str) -> None:
        with self._lock:
            _, size = self._items.pop(str(key))
            self._bytes -= size

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._items.keys()))

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[str(key)]
        except KeyError:
            return default

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            record = self._items.pop(str(key), None)
            if record is None:
                return default
            self._bytes -= record[1]
            return record[0]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def remove_if(self, predicate: Callable[[str, Any], bool]) -> int:
        removed = 0
        with self._lock:
            for key, (value, size) in list(self._items.items()):
                if predicate(key, value):
                    self._items.pop(key, None)
                    self._bytes -= size
                    removed += 1
        return removed

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "items": len(self._items),
                "bytes": self._bytes,
                "max_items": self.max_items,
                "max_bytes": self.max_bytes,
                "evictions": self._evictions,
            }

    def _evict(self) -> None:
        while self._items and (
            len(self._items) > self.max_items or self._bytes > self.max_bytes
        ):
            _, (_, size) = self._items.popitem(last=False)
            self._bytes -= size
            self._evictions += 1
