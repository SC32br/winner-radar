"""Регионы сбора: КЛАДР, даты, угадывание области по тексту."""

from __future__ import annotations

from datetime import date
from typing import Any
import re


def _fold(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def months_ago(months: int, today: date | None = None) -> date:
    months = max(1, int(months or 12))
    stamp = today or date.today()
    year = stamp.year
    month = stamp.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(stamp.day, _month_days(year, month))
    return date(year, month, day)


def _month_days(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 31 if month in {1, 3, 5, 7, 8, 10, 12} else 30


def cutoff_iso(months: int, today: date | None = None) -> str:
    return months_ago(months, today).isoformat()


def cutoff_ru(months: int, today: date | None = None) -> str:
    stamp = months_ago(months, today)
    return stamp.strftime("%d.%m.%Y")


def today_ru(today: date | None = None) -> str:
    return (today or date.today()).strftime("%d.%m.%Y")


def parse_date_any(raw: str) -> str | None:
    text = (raw or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    match = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", text)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def iso_to_ru(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{day}.{month}.{year}"


def collect_window(collect_cfg: dict[str, Any] | None) -> tuple[str, str]:
    cfg = collect_cfg or {}
    iso = parse_date_any(str(cfg.get("date_from") or ""))
    if iso:
        return iso, iso_to_ru(iso)
    months = int(cfg.get("months") or 12)
    return cutoff_iso(months), cutoff_ru(months)


def date_ok(signed_at: str | None, published_at: str | None, cutoff: str) -> bool:
    stamp = (signed_at or published_at or "")[:10]
    if not stamp or not cutoff:
        return True
    return stamp >= cutoff


def regions_from(filters: dict[str, Any]) -> list[dict[str, Any]]:
    rows = filters.get("regions") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        kladr = str(row.get("kladr") or "").strip()
        name = str(row.get("name") or "").strip()
        if not code or not kladr or not name:
            continue
        geo = [str(item).strip() for item in (row.get("geo") or []) if str(item).strip()]
        out.append(
            {
                "code": code,
                "kladr": kladr,
                "name": name,
                "short": str(row.get("short") or name).strip(),
                "geo": geo,
            }
        )
    return out


def geo_words_from(filters: dict[str, Any]) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for region in regions_from(filters):
        for word in [region["name"], region["short"], *region["geo"]]:
            key = _fold(word)
            if key and key not in seen:
                seen.add(key)
                words.append(word)
    for word in filters.get("geo_words") or []:
        key = _fold(str(word))
        if key and key not in seen:
            seen.add(key)
            words.append(str(word))
    return words


def eis_place_cfg(filters: dict[str, Any]) -> dict[str, Any]:
    eis = dict(filters.get("eis") or {})
    regions = regions_from(filters)
    if regions:
        eis["customer_place"] = ",".join(item["kladr"] for item in regions)
        eis["customer_place_codes"] = ",".join(item["code"] for item in regions)
    return eis


def _contains_word(blob: str, needle: str) -> bool:
    if not needle:
        return False
    start = 0
    while True:
        idx = blob.find(needle, start)
        if idx < 0:
            return False
        before = blob[idx - 1] if idx > 0 else " "
        after = blob[idx + len(needle)] if idx + len(needle) < len(blob) else " "
        if not before.isalpha() and not after.isalpha():
            return True
        start = idx + 1


def _name_forms(name: str) -> list[str]:
    folded = _fold(name)
    forms = [name]
    if folded.endswith("ая область"):
        stem = name[: -len("ая область")].rstrip()
        forms.extend(
            [
                f"{stem}ой области",
                f"{stem}ую область",
                f"{stem}ое областное",
            ]
        )
    if folded.endswith("ский край"):
        stem = name[: -len("ский край")].rstrip()
        forms.extend(
            [
                f"{stem}ского края",
                f"{stem}ском крае",
                f"{stem}ский край",
            ]
        )
    if "карелия" in folded:
        forms.extend(["Республики Карелия", "Республике Карелия", "Карелии"])
    if "санкт-петербург" in folded:
        forms.extend(
            [
                "Санкт-Петербурга",
                "Санкт-Петербурге",
                "Петербурга",
                "Петербурге",
            ]
        )
    if folded == "москва":
        forms.extend(["города Москвы", "г. Москвы", "г. Москве", "в Москве"])
    return forms


def _city_stems(word: str) -> list[str]:
    folded = _fold(word)
    out = [word]
    if len(folded) >= 6 and folded[-1] in "аяь":
        out.append(folded[:-1])
    return out


def infer_region(text: str, filters: dict[str, Any]) -> dict[str, Any] | None:
    blob = _fold(text)
    if not blob:
        return None
    ranked: list[tuple[int, dict[str, Any]]] = []
    for region in regions_from(filters):
        needles: list[str] = []
        for item in [region["name"], region["short"], *region["geo"]]:
            needles.extend(_name_forms(item))
            needles.extend(_city_stems(item))
        needles = sorted({_fold(item) for item in needles if item}, key=len, reverse=True)
        for folded in needles:
            if len(folded) < 4:
                continue
            hit = _contains_word(blob, folded) if len(folded) < 6 else folded in blob
            if hit:
                ranked.append((len(folded), region))
                break
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def infer_region_from_inn(inn: str | None, filters: dict[str, Any]) -> dict[str, Any] | None:
    raw = "".join(ch for ch in str(inn or "") if ch.isdigit())
    if len(raw) not in {10, 12}:
        return None
    prefix = raw[:2]
    for region in regions_from(filters):
        if region["code"].zfill(2) == prefix:
            return region
    return None


def apply_region(
    hit_text: str,
    filters: dict[str, Any],
    extra: dict[str, Any] | None = None,
    inn: str | None = None,
) -> dict[str, Any]:
    payload = dict(extra or {})
    if payload.get("region_code") and payload.get("region_text"):
        return payload
    region = infer_region(hit_text, filters)
    if region is None:
        region = infer_region_from_inn(inn or payload.get("customer_inn"), filters)
    if region:
        payload["region_code"] = region["code"]
        payload["region_text"] = region["name"]
        payload["region_short"] = region["short"]
    return payload
