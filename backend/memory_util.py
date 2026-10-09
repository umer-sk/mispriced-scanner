"""Hand freed memory back to the OS after a scan.

A scan parses large JSON payloads on executor threads. glibc keeps a malloc
arena per thread and rarely returns freed pages, so RSS ratchets up across a
scan even though the live Python objects are small (measured: ~20 MB live vs
+120 MB RSS). malloc_trim(0) releases the free pages. It is Linux/glibc only
and a no-op everywhere else.
"""
import ctypes
import gc
import logging

logger = logging.getLogger(__name__)


def release_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass  # not glibc (macOS, musl) — nothing to trim
