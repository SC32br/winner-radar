"""Грубый отсев до карточки: свои ИНН, сумма, минус-слова, ключи / ОКПД."""

from __future__ import annotations

import re
from typing import Any

GROUP_HINTS: dict[str, tuple[str, ...]] = {
    "object": (
        "строительство здания",
        "детского сада",
        "строительство школы",
        "жилого дома",
        "многоквартирного",
        "капитальный ремонт здания",
        "реконструкция здания",
        "возведение здания",
        "строительство корпуса",
        "строительство пристройки",
        "строительство склада",
        "строительство ангара",
        "строительство дошкольного",
        "строительство поликлиники",
        "строительно-монтажные",
    ),
    "geodesy": (
        "разбивка осей",
        "разбивочн",
        "геодезическое сопровождение",
        "геодезические работы",
        "исполнительная съемка",
        "вынос пятна",
        "вынос в натуру",
    ),
    "earth": (
        "земляные",
        "грунта",
        "котлован",
        "нулевой цикл",
        "планировка",
        "засыпка",
        "отсыпка",
        "транше",
        "инженерные сети",
        "наружные сети",
        "прокладка сетей",
        "водопонижен",
        "шпунт",
        "выемка",
        "подготовка основания",
        "подготовка строительной",
    ),
    "foundation": (
        "сваи",
        "свай",
        "ростверк",
        "фундамент",
        "забивн",
        "буронабивн",
        "буроинъекц",
    ),
    "piles": ("сваи", "ростверк"),
    "monolith": (
        "монолит",
        "бетонирован",
        "опалубка",
        "армирование",
        "железобетон",
        "плита перекрытия",
        "противомороз",
    ),
    "box": ("сэндвич", "металлокаркас", "газобетон", "газоблок", "быстровозводим"),
}


def _fold(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def keyword_in_subject(subject: str, keyword: str) -> bool:
    blob = _fold(subject)
    needle = _fold(keyword)
    if needle and needle in blob:
        return True
    keys = [item for item in re.findall(r"[а-яa-z0-9]+", needle) if len(item) >= 4]
    if not keys:
        return False
    words = re.findall(r"[а-яa-z0-9]+", blob)
    if len(keys) == 1:
        token = keys[0]
        if len(token) < 6:
            return False
        if len(token) >= 10:
            prefix = token[:8]
        elif len(token) >= 8:
            prefix = token[:7]
        else:
            prefix = token[:6]
        return any(word.startswith(prefix) for word in words if len(word) >= len(prefix))
    key_stems = [item[:5] for item in keys]
    subj_stems = [item[:5] for item in words]
    return _stems_near(subj_stems, key_stems, gap=1)


def _stems_near(hay: list[str], needle: list[str], gap: int) -> bool:
    if _stems_ordered(hay, needle, gap):
        return True
    if len(needle) == 2 and _stems_ordered(hay, list(reversed(needle)), gap):
        return True
    return False


def _stems_ordered(hay: list[str], needle: list[str], gap: int) -> bool:
    if not needle:
        return False
    limit = len(hay)
    for start in range(limit):
        cursor = start
        ok = True
        for token in needle:
            found = None
            end = min(limit, cursor + gap + 1)
            for idx in range(cursor, end):
                if hay[idx] == token:
                    found = idx
                    break
            if found is None:
                ok = False
                break
            cursor = found + 1
        if ok:
            return True
    return False


def matched_keywords(subject: str, keywords: list[str]) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        if keyword_in_subject(subject, str(keyword)) and keyword not in hits:
            hits.append(keyword)
    return hits


def profiles_for(keywords: list[str]) -> list[str]:
    blob = _fold(" ".join(keywords))
    found: list[str] = []
    for name, hints in GROUP_HINTS.items():
        if any(hint in blob for hint in hints):
            found.append(name)
    return found


def okpd_hit(codes: str | None, prefixes: list[str]) -> bool:
    if not codes or not prefixes:
        return False
    parts = [p.strip() for p in codes.replace(";", ",").split(",") if p.strip()]
    for code in parts:
        for prefix in prefixes:
            if code.startswith(prefix) or prefix.startswith(code):
                return True
    return False


def own_inn(inns: list[str | None], own: list[str]) -> bool:
    own_set = {item.strip() for item in own if item}
    return any(inn and inn in own_set for inn in inns)


def _minus_hit(subject: str, minus: str) -> bool:
    needle = _fold(minus)
    if not needle:
        return False
    if needle in _fold(subject):
        return True
    tokens = [item for item in re.findall(r"[а-яa-z0-9]+", needle) if len(item) >= 4]
    if len(tokens) >= 2:
        return keyword_in_subject(subject, minus)
    return False


def decide(
    *,
    subject: str,
    amount_rub: int | None,
    customer_inn: str | None,
    winner_inn: str | None,
    okpd_codes: str = "",
    filters: dict[str, Any],
) -> tuple[bool, str, list[str]]:
    if own_inn([customer_inn, winner_inn], list(filters.get("own_inns") or [])):
        return False, "own_inn", []
    amount_min = int(filters.get("amount_min") or 500000)
    if amount_rub is None or amount_rub < amount_min:
        return False, "amount", []
    for minus in filters.get("exclude_keywords") or []:
        if _minus_hit(subject, str(minus)):
            return False, f"exclude:{minus}", []
    hits = matched_keywords(subject, list(filters.get("keywords") or []))
    if hits or okpd_hit(okpd_codes, list(filters.get("okpd_prefixes") or [])):
        return True, "ok", hits
    return False, "no_keyword", []


def geo_ok(text: str, geo_words: list[str]) -> bool:
    blob = _fold(text)
    ranked = sorted(
        [_fold(word) for word in geo_words if word and len(_fold(str(word))) >= 4],
        key=len,
        reverse=True,
    )
    return any(word in blob for word in ranked)
