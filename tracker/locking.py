from __future__ import annotations

import msvcrt
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

RETRY_SECONDS = 0.25


def lock_path_for(cfg: dict) -> Path:
    state_path = Path(cfg.get("state_path") or (Path(__file__).resolve().parent / "state.json"))
    return state_path.parent / "tracker.lock"


@contextmanager
def file_lock(path: str | Path, timeout: float = 180) -> Iterator[Path]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = open(target, "a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("could not lock %s within %ss" % (target, timeout))
                time.sleep(RETRY_SECONDS)
        try:
            yield target
        finally:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        handle.close()
