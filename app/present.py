"""Подписи и срочность звонка для витрины. Пустые поля — словами, не null."""

from __future__ import annotations

import json
from datetime import datetime, timezone

MISSING = "нет в источнике"
NO_PHONE = "телефона в документах нет"
NO_EMAIL = "почты в документах нет"

STATUS_LABELS = {
    "new": "Новый",
    "watching": "Смотрю",
    "take": "Беру",
    "reject": "Не то",
    "done": "Готово",
}

PROFILE_LABELS = {
    "object": "Стройка объекта",
    "geodesy": "Геодезия",
    "earth": "Земля и сети",
    "foundation": "Фундамент и сваи",
    "piles": "Сваи и ростверк",
    "monolith": "Монолит и бетон",
    "box": "Коробка",
}

SOURCE_LABELS = {
    "eis": "Госзакупки",
    "mos": "Портал Москвы",
    "clearspending": "История контрактов",
}

CONTACT_SOURCE_LABELS = {
    "eis": "ЕИС",
    "eis_card": "ЕИС",
    "eis_participants": "ЕИС",
    "checko": "Checko",
    "document_ocr": "файл",
    "email_domain": "из почты",
    "winner_site": "сайт фирмы",
    "dadata": "налоговая",
    "clearspending": "история",
}

ORG_STATUS_RU = {
    "ACTIVE": "действует",
    "LIQUIDATED": "ликвидирована",
    "LIQUIDATING": "ликвидируется",
    "BANKRUPT": "банкрот",
    "REORGANIZING": "реорганизация",
}


def parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [str(item) for item in data if item]
    return []


def money(amount: int | None) -> str:
    if amount is None:
        return MISSING
    text = f"{amount:,}".replace(",", " ")
    return f"{text} ₽"


def ru_date(raw: str | None) -> str:
    if not raw:
        return MISSING
    text = raw[:10]
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return raw
    return parsed.strftime("%d.%m.%Y")


def contact_source_label(raw: str | None) -> str:
    key = (raw or "").strip()
    return CONTACT_SOURCE_LABELS.get(key, key or "неизвестно")


def source_labels(raw: str | None) -> list[str]:
    out: list[str] = []
    for part in (raw or "").split(","):
        key = part.strip()
        if not key:
            continue
        label = SOURCE_LABELS.get(key, key)
        if label not in out:
            out.append(label)
    return out or [MISSING]


def profile_labels(raw: str | None) -> list[str]:
    keys = parse_json_list(raw)
    labels = [PROFILE_LABELS.get(key, key) for key in keys]
    return labels or [MISSING]


def grouped_keywords(keywords: list[str]) -> list[dict[str, list[str] | str]]:
    """Ключи фильтра по видам работ, каждый ключ один раз."""
    from app.prefilter import GROUP_HINTS

    buckets: dict[str, list[str]] = {key: [] for key in GROUP_HINTS}
    other: list[str] = []
    seen: set[str] = set()
    for word in keywords:
        text = str(word).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        blob = text.lower().replace("ё", "е")
        hit = next(
            (name for name, hints in GROUP_HINTS.items() if any(hint in blob for hint in hints)),
            None,
        )
        if hit:
            buckets[hit].append(text)
        else:
            other.append(text)
    out: list[dict[str, list[str] | str]] = []
    for name, rows in buckets.items():
        if rows:
            out.append({"label": PROFILE_LABELS.get(name, name), "words": rows})
    if other:
        out.append({"label": "Прочее", "words": other})
    return out


def days_ago(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def urgency(
    *,
    amount: int | None,
    signed_at: str | None,
    published_at: str | None,
    has_phone: bool,
    has_email: bool,
    has_winner: bool,
    profiles: list[str],
) -> float:
    score = 0.15
    if amount is None:
        pass
    elif amount >= 5_000_000:
        score += 0.28
    elif amount >= 1_000_000:
        score += 0.16
    elif amount >= 500_000:
        score += 0.08
    if has_phone:
        score += 0.22
    elif has_email:
        score += 0.1
    if has_winner:
        score += 0.1
    if any(item in {"geodesy", "earth", "piles", "monolith"} for item in profiles):
        score += 0.12
    age = days_ago(signed_at or published_at)
    if age is None:
        pass
    elif age <= 90:
        score += 0.18
    elif age <= 365:
        score += 0.08
    return round(min(1.0, score), 2)


def is_hot(score: float) -> bool:
    return score >= 0.65
