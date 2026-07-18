"""Approximate token counting for chunk budgeting.

A fast heuristic (~4 chars/token) — good enough for sizing chunks.
"""

from __future__ import annotations

import math


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))
