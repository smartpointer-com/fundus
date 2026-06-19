"""Approximate token counting for chunk budgeting.

A fast heuristic (~4 chars/token) by default — good enough for sizing chunks.
Install the ``tokenize`` extra and plug a real tokenizer for exact counts.
"""

from __future__ import annotations

import math


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))
