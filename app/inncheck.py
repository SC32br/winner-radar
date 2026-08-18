"""ИНН: 10 или 12 цифр и контрольная сумма. ИКЗ не пропускать."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\D+")


def normalize_inn(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = _DIGITS.sub("", raw)
    if len(digits) not in (10, 12):
        return None
    if not _checksum_ok(digits):
        return None
    return digits


def _checksum_ok(digits: str) -> bool:
    nums = [int(ch) for ch in digits]
    if len(nums) == 10:
        return _n10(nums[:9]) == nums[9]
    return _n10(nums[:10], (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == nums[10] and _n10(
        nums[:11], (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    ) == nums[11]


def _n10(nums: list[int], weights: tuple[int, ...] | None = None) -> int:
    w = weights or (2, 4, 10, 3, 5, 9, 4, 6, 8)
    return sum(n * w[i] for i, n in enumerate(nums)) % 11 % 10
