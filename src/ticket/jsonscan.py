"""Pull JSON documents out of JavaScript source (inline <script> blocks).

Sites often embed their initial state as `window.__STATE__ = {...}`, `JSON.parse("...")`, or
framework payloads like `self.__next_f.push([1, "..."])`. This finds decodable JSON literals
after `=`, `(`, `:` and inside long string literals. It's generic and deliberately
conservative: only valid JSON of at least `min_len` characters is returned.
"""

from __future__ import annotations

import json
import re
from typing import Any

_START = re.compile(r"[=(:,]\s*([\{\[])")
_IN_STRING_START = re.compile(r"[\{\[]\s*\"")
_DECODER = json.JSONDecoder()


def _scan(text: str, pattern: re.Pattern, group: int, min_len: int, max_attempts: int) -> list[tuple[int, int, Any]]:
    found = []
    pos, attempts = 0, 0
    while attempts < max_attempts:
        m = pattern.search(text, pos)
        if not m:
            break
        start = m.start(group) if group else m.start()
        attempts += 1
        try:
            obj, end = _DECODER.raw_decode(text, start)
        except ValueError:
            pos = start + 1
            continue
        if end - start >= min_len:
            found.append((start, end, obj))
            pos = end
        else:
            pos = start + 1
    return found


def _long_strings(obj: Any, min_len: int, depth: int = 0):
    if depth > 6:
        return
    if isinstance(obj, str) and len(obj) >= min_len:
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _long_strings(v, min_len, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _long_strings(v, min_len, depth + 1)


def json_in_script(text: str, min_len: int = 500, max_attempts: int = 20_000, _depth: int = 0) -> list[Any]:
    out: list[Any] = []
    hits = _scan(text, _START, 1, min_len, max_attempts)
    if _depth > 0:  # inside a decoded string: JSON can start anywhere
        hits += _scan(text, _IN_STRING_START, 0, min_len, max_attempts)
    for _, _, obj in hits:
        out.append(obj)
        if _depth < 2:
            for s in _long_strings(obj, min_len):
                out.extend(json_in_script(s, min_len, max_attempts, _depth + 1))
    return out
