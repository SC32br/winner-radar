"""Саммари файла и разбор: наш ли заказ, по каким работам."""

from __future__ import annotations

import json
import re
from typing import Any

from app.prefilter import GROUP_HINTS, _fold
from app.present import PROFILE_LABELS, money

_MONEY = re.compile(r"(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{4,6}),(\d{2})")
_SPACE = re.compile(r"\s+")
_CYR = re.compile(r"[а-яёА-ЯЁ]")
_LAT = re.compile(r"[a-zA-Z]")
_WORD = re.compile(r"[а-яё]{4,}", re.I)

_KIND_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("удостоверяющий лист", re.compile(r"информационно-удостоверяющ|удостоверяющий\s+лист", re.I)),
    ("платёжка", re.compile(r"платежн\w+\s+поручен", re.I)),
    ("контракт", re.compile(r"муниципальн\w+\s+контракт|государственн\w+\s+контракт|\bконтракт\s*№", re.I)),
    ("техзадание", re.compile(r"техническ\w+\s+задан", re.I)),
    ("рабочая документация", re.compile(r"рабочая\s+документация", re.I)),
    ("техотчёт", re.compile(r"технический\s+отчет|технический\s+отчёт", re.I)),
    ("смета", re.compile(r"\bсмет", re.I)),
    ("проект", re.compile(r"проектн\w+\s+документац|раздел\s+пд", re.I)),
]

_PD_HINTS: list[tuple[str, re.Pattern[str], str | None]] = [
    ("ПЗУ", re.compile(r"пзу|планировочн\w+\s+организ", re.I), None),
    ("ИОС", re.compile(r"иос\d?|инженерн\w+\s+сет|инженерно-техническ", re.I), "earth"),
    ("КР", re.compile(r"\bкр\d?\b|конструктивн\w+\s+решен", re.I), "foundation"),
    ("ОДИ", re.compile(r"\bоди\b|доступ\w+\s+инвалид", re.I), None),
]

_MINUS = (
    "кадастр",
    "межеван",
    "генплан",
    "землеустрой",
    "поставка товара",
    "ремонт кровли",
    "асфальт",
    "благоустрой",
)

_OUR_ORDER = ("geodesy", "earth", "foundation", "piles", "monolith", "box", "object")
_CORE = ("geodesy", "earth", "foundation", "piles", "monolith")

_WORK_ON = re.compile(
    r"(?:на выполнение|предмет[:\s]|на оказание|работы по)\s+(.{12,180}?)(?:\.|«|$)",
    re.I | re.S,
)
_SHEET_NAME = re.compile(
    r"Наименование документа\s+(.{8,220}?)(?:\s+Версия|\s+Номер|\s+Том|\s+MD5|$)",
    re.I | re.S,
)


def ocr_is_garbage(text: str) -> bool:
    blob = text or ""
    cyr = len(_CYR.findall(blob))
    lat = len(_LAT.findall(blob[:4000]))
    words = _WORD.findall(blob[:8000])
    if cyr < 50:
        return True
    if lat > cyr * 1.2 and cyr < 400:
        return True
    if len(words) < 8:
        return True
    return False


def _clean(text: str) -> str:
    return _SPACE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def _kind_of(text: str, filename: str) -> str:
    blob = f"{filename or ''} {text[:2500]}"
    for kind, pattern in _KIND_RULES:
        if pattern.search(blob):
            return kind
    if ocr_is_garbage(text):
        return "скан не разобрали"
    return "документ"


def _clip(text: str, limit: int = 320) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def _file_title(name: str, fallback: str) -> str:
    text = (name or "").strip()
    if not text or text.lower().startswith("file.") or text.endswith("..."):
        return fallback
    return text[:80]


def summarize_document(text: str, filename: str = "", *, status: str = "") -> dict[str, Any]:
    raw = text or ""
    name = filename or ""
    if not raw.strip():
        if status == "skipped_type":
            return _sum_out(
                "пропуск",
                _file_title(name, "Файл"),
                "Этот тип не читаем (архив RAR и подобное). Не открывать этим ридером.",
                "skip",
                rank=49,
            )
        if _looks_like_archive(name):
            return _sum_out(
                "архив",
                _file_title(name, "Архив приложений"),
                "Архив ещё не разобрали. Внутри может быть смета — открыть глазами.",
                "read",
                rank=8,
            )
        return _sum_out(
            "пусто",
            _file_title(name, "Файл"),
            "Файл пустой, текста нет. Не открывать.",
            "skip",
            rank=49,
        )
    blob = _fold(f"{name}\n{raw[:2500]}")
    ds = _ds_number(name, raw)
    stages = [
        item
        for item in work_hits(raw)
        if item.get("profile") in _CORE and item.get("amount_text")
    ]
    stage_bits = []
    for item in stages:
        pay = item.get("amount_text") or ""
        ev = item.get("evidence") or item.get("label") or ""
        stage_bits.append(f"{ev} {pay}".strip() if pay else ev)

    if _is_control_doc(name, raw):
        return _sum_out("контроль", "Проверка финансов", "Автогалочка контроля. Сметы нет. Не открывать.", "skip", rank=48)
    if _is_eis_print(name, raw):
        title = f"Печатная форма ДС №{ds}" if ds else "Печатная форма контракта"
        return _sum_out("печатная форма", title, "Карточка ЕИС, дубль PDF. Не открывать.", "skip", rank=46)
    if _is_eis_xml(name, raw):
        title = f"XML ДС №{ds}" if ds else "XML контракта"
        return _sum_out("xml", title, "Выгрузка кодов ЕИС. Не открывать.", "skip", rank=47)
    if re.search(r"документы.{0,8}явля", _fold(name)):
        title = f"Копия ДС №{ds}" if ds else "Копия приложения"
        return _sum_out("дубль", title, "Копия приложения. Не открывать.", "skip", rank=45)

    kind = _kind_of(raw, name)
    garbage = kind == "скан не разобрали" or ocr_is_garbage(raw)
    if garbage and kind != "платёжка":
        return _sum_out(
            "скан не разобрали",
            "Скан",
            "Текст с картинки не сложился. Пользы нет, пока не откроете глазами.",
            "skip",
            garbage=True,
            rank=49,
        )
    if kind == "платёжка":
        pay = re.search(r"сумма[^\d]{0,40}([\d\s]{3,})[,\-](\d{2})", raw, re.I)
        money_bit = f" на {_clean(pay.group(1))} руб." if pay else ""
        return _sum_out("платёжка", "Платёжка", f"Платёжное поручение{money_bit}. Это не смета.", "skip", rank=36)
    if kind == "удостоверяющий лист":
        return _sum_out(
            kind,
            "Удостоверяющий лист",
            "Титул тома, не смета.",
            "skip",
            rank=38,
        )

    if ds:
        title = f"Допсоглашение №{ds}"
        if stage_bits:
            return _sum_out("смета", title, "Смета: " + "; ".join(stage_bits[:4]) + ".", "read", rank=0)
        if re.search(r"расчетн\w+\s+счет|банковск\w+\s+реквизит|реквизит\w*.{0,40}оплат|\bр/с\b", blob):
            return _sum_out("дс", title, "Смена расчётного счёта. Сметы нет.", "skip", rank=35)
        term = re.search(r"до\s+(\d{2}\.\d{2}\.\d{4})", raw)
        if re.search(r"срок|график платежей|лимит", blob):
            when = f" Срок до {term.group(1)}." if term else ""
            return _sum_out("дс", title, f"Сроки и график оплаты.{when} Сметы нет.", "skip", rank=30)
        own = re.search(
            r"устройств\w+\s+фундамент[\s\S]{0,500}?составляет\s+([\d\s]{3,},\d{2})\s*руб",
            raw,
            re.I,
        )
        if own:
            return _sum_out(
                "дс",
                title,
                f"Сам делает фундаменты и основания — {_clean(own.group(1))} ₽.",
                "read",
                rank=1,
            )
        nds = re.search(r"ндс\s+(\d{1,2})\s*%", blob)
        if nds:
            return _sum_out("дс", title, f"Ставка НДС {nds.group(1)}%. Ведомости нет.", "skip", rank=32)
        return _sum_out("дс", title, "Допник без ведомости наших работ.", "skip", rank=34)

    if stage_bits:
        return _sum_out(
            "смета",
            "Ведомость в договоре",
            "Смета: " + "; ".join(stage_bits[:4]) + ".",
            "read",
            rank=0,
        )
    named = _smeta_named_totals(raw)
    if named:
        title = "ЛСР в архиве приложений" if "приложен" in _fold(name) else "Смета"
        return _sum_out("смета", title, _smeta_line(named), "read", rank=0)
    if re.search(r"\bлср\b|грант.?смет|локальн\w+\s+сметн", blob):
        title = "ЛСР в архиве приложений" if "приложен" in _fold(name) or "лср.pdf" in _fold(raw[:300]) else "ЛСР"
        return _sum_out(
            "смета",
            title,
            "ЛСР есть, цифры этапов из текста не сложились.",
            "read",
            rank=2,
        )
    if kind == "контракт" or re.search(r"контракт|договор", blob):
        if re.search(r"локальн\w+\s+сметн|сметн\w+\s+расчет|приложени[ея]\s*№\s*\d+", blob):
            return _sum_out(
                "контракт",
                "Текст договора",
                "Договор без ведомости. Смета только названа приложением, самого расчёта в файле нет.",
                "skip",
                rank=40,
            )
        return _sum_out("контракт", "Текст договора", "Договор без ведомости. Не открывать.", "skip", rank=40)
    title = name[:60] or "файл"
    if re.search(r"^\d[\d\s]{8,}", title) or title.lower().startswith("file."):
        title = "файл"
    return _sum_out(kind or "документ", title, "Цифр по нашим этапам нет.", "skip", rank=42)


def _sum_out(
    kind: str, title: str, text: str, value: str, *, garbage: bool = False, rank: int | None = None
) -> dict[str, Any]:
    return {
        "kind": kind[:80],
        "title": _clip(title, 80),
        "text": _clip(text, 280),
        "value": value,
        "rank": 0 if rank is None and value == "read" else (50 if rank is None else rank),
        "garbage": garbage,
        "via": "text",
    }


def _ds_number(name: str, text: str) -> str | None:
    blob = f"{name} {text[:2000]}"
    for pattern in (
        r"доп\.?\s*соглашени[ея]\s*(?:№\s*)?(\d+)",
        r"\bДС[_\s.-]*№?\s*(\d+)",
        r"номер доп\.?\s*соглашения:\s*(\d+)",
    ):
        match = re.search(pattern, blob, re.I)
        if match:
            return match.group(1)
    return None


def _contract_number(name: str, text: str) -> str | None:
    match = re.search(r"(\d{2,}/[А-ЯA-Z]{1,5}-\d{2})", f"{name} {text[:2500]}", re.I)
    return match.group(1) if match else None


def _is_control_doc(name: str, text: str) -> bool:
    blob = _fold(f"{name} {text[:1200]}")
    return bool(
        re.search(r"результат контроля|уведомление о п|комитет финансов|дополнительный контроль не требуется|аис бп", blob)
    )


def _is_eis_print(name: str, text: str) -> bool:
    blob = _fold(f"{name} {text[:800]}")
    if "печатная форма" in blob:
        return True
    if name.lower().endswith(".html") and "еис" in blob:
        return True
    return False


def _is_eis_xml(name: str, text: str) -> bool:
    if name.lower().endswith(".xml"):
        return True
    head = _clean(text[:240])
    if re.match(r"^[\d\s]{6,}\d{2}/", head):
        return True
    if "true 01722" in _fold(head) or re.match(r"^[01]\s+\d+\s+true\s+", head, re.I):
        return True
    return False


def _price_near_contract(text: str) -> str | None:
    folded = _fold(text[:8000])
    match = re.search(
        r"(?:цена контракта|цена настоящего контракта|стоимость контракта)[^\d]{0,40}([\d\s]{5,}),(\d{2})",
        folded,
        re.I,
    )
    if match:
        return money(int(re.sub(r"\D", "", match.group(1)) or 0))
    return None


def _object_bit(text: str) -> str:
    match = re.search(
        r"центр(?:а)? социальной реабилитации[^.]{0,50}",
        text[:5000],
        re.I,
    )
    if match:
        return _clip(_clean(match.group(0)), 90)
    return ""


_SYS_SUM = """Ты читаешь ОДИН файл закупки для звонка победителю (субподряд стройки).
Нужна польза: открывать этот файл или нет, и какие цифры/факты внутри.
Ответь ТОЛЬКО JSON:
{"kind":"контракт|дс|смета|печатная форма|xml|контроль|другое","title":"коротко, без ИНН и адреса","text":"1-2 фразы: суть + цифры. Если это дубль карточки ЕИС или проверка финансов — так и скажи: открывать не надо.","value":"read|skip"}
Запрещено: «файл содержит», «документ представляет собой», адреса, ИНН, ИКЗ, вода про «порядок взаимодействия сторон». Не выдумывай суммы."""

_SYS_LEAD = """Ты оцениваешь закупку для звонка победителю: предлагаем субподряд по своим этапам.
Мы не участвуем в торгах. Звоним победителю стройки и предлагаем свои этапы.

Наши этапы (пиши в works только те, что ЕСТЬ в файлах, с короткой цитатой):
geodesy — геодезия, разбивка осей, исполнительная съёмка
earth — земля, котлован, наружные сети, шпунт
foundation — фундамент, основания
piles — сваи, ростверк
monolith — монолит, бетон, опалубка, перекрытия
box — коробка, сэндвич, металлокаркас (второй эшелон)

Не наш: кадастр, межевание, генплан как цель, поставка товара, кровля, клининг.
Не пиши profile=object, если есть конкретные этапы.
why: 2–4 предложения. Сначала что за объект. Потом какие наши этапы стоят в договоре/смете. Без фраз «подходит для предложения услуг», «комплекс общестроительных работ», «можно предлагать».
label: коротко, что за объект, без суммы если она уже в карточке.
Ответь ТОЛЬКО JSON:
{"verdict":"yes|no|maybe","label":"...","why":"...","works":[{"profile":"earth","label":"Земля и сети","evidence":"цитата"}],"minus":[]}"""


def enrich_summary(text: str, filename: str = "") -> dict[str, Any]:
    base = summarize_document(text, filename)
    base.setdefault("via", "text")
    if base.get("value") in {"read", "skip"} or base.get("garbage"):
        return base
    from app import llm

    if not llm.enabled():
        return base
    clip = _clip(text or "", 3500)
    data = llm.chat_json(_SYS_SUM, f"Файл: {filename}\n\nТекст:\n{clip}")
    if not data:
        return base
    kind = str(data.get("kind") or base.get("kind") or "документ")
    title = str(data.get("title") or base.get("title") or filename or "файл")
    body = str(data.get("text") or base.get("text") or "")
    if len(body) < 20:
        return base
    value = str(data.get("value") or base.get("value") or "skip")
    if value not in {"read", "skip"}:
        value = "skip"
    return {
        "kind": kind[:80],
        "title": _clip(title, 160),
        "text": _clip(body, 420),
        "value": value,
        "rank": int(base.get("rank") or (0 if value == "read" else 50)),
        "garbage": False,
        "via": "llm",
    }


_STAGE_KEYS = (
    "геодезич",
    "геодез",
    "разбивк",
    "земляные",
    "котлован",
    "наружные сети",
    "инженерные сети",
    "фундамент",
    "свай",
    "ростверк",
    "монолит",
    "опалубк",
    "железобетон",
    "плита перекрытия",
)


def _stage_window(text: str, limit: int = 2200) -> str:
    raw = text or ""
    folded = _fold(raw)
    parts: list[str] = []
    for key in _STAGE_KEYS:
        idx = folded.find(key)
        if idx < 0:
            continue
        chunk = _clean(raw[max(0, idx - 70) : idx + 160])
        if chunk and chunk not in parts:
            parts.append(chunk)
        if len(parts) >= 8:
            break
    if parts:
        return _clip(" | ".join(parts), limit)
    return _clip(raw, limit)


def _merge_works(rule: list[dict[str, str]], llm: list[dict[str, str]]) -> list[dict[str, str]]:
    by_profile: dict[str, dict[str, str]] = {}
    for item in rule:
        key = item.get("profile") or ""
        if key:
            by_profile[key] = item
    for item in llm:
        key = item.get("profile") or ""
        if not key:
            continue
        if key == "object" and any(p in by_profile for p in _CORE):
            continue
        current = by_profile.get(key)
        if current is None:
            by_profile[key] = item
            continue
        ev = str(item.get("evidence") or "")
        old = str(current.get("evidence") or "")
        if len(ev) > 20 and ("из предмета" in old or len(ev) > len(old)):
            by_profile[key] = {
                **current,
                "evidence": ev,
                "label": item.get("label") or current.get("label"),
            }
    order = list(_OUR_ORDER)
    out: list[dict[str, str]] = []
    for key in order:
        if key in by_profile:
            out.append(by_profile[key])
    for key, item in by_profile.items():
        if key not in order:
            out.append(item)
    if any(item.get("profile") in _CORE for item in out):
        out = [item for item in out if item.get("profile") != "object"]
    return out


def _why_from_works(works: list[dict[str, str]], *, object_line: str, llm_why: str) -> str:
    core = [item for item in works if item.get("profile") in _CORE]
    if not core:
        text = _clean(llm_why or object_line)
        return _clip(text, 520)
    bits = []
    for item in core:
        quote = _clip(item.get("evidence") or "", 90)
        pay = item.get("amount_text") or ""
        if pay:
            bits.append(f"{item['label']} — {quote}, {pay}")
        else:
            bits.append(f"{item['label']} — {quote}")
    head = _clip(_clean(object_line), 160)
    why = ""
    if head:
        why = head.rstrip(".") + ". "
    why += "В договоре и смете стоят наши этапы: " + "; ".join(bits) + "."
    return _clip(why, 800)


def enrich_lead(base: dict[str, Any], *, subject: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
    from app import llm

    blob = " ".join(str(item.get("ocr_text") or item.get("summary") or "") for item in docs)
    rule_works = work_hits(blob) or list(base.get("works") or [])
    base.setdefault("via", "text")
    if not llm.enabled():
        works = _merge_works(rule_works, [])
        base["works"] = works
        base["why"] = _why_from_works(works, object_line=subject, llm_why=str(base.get("why") or ""))
        if any(item.get("profile") in _CORE for item in works):
            base["verdict"] = "yes"
            labels = [item["label"] for item in works if item.get("profile") in _CORE]
            base["label"] = "Наш заход: " + ", ".join(labels)
        return base
    parts = []
    for item in docs[:10]:
        kind = item.get("kind") or ""
        body = _stage_window(str(item.get("ocr_text") or item.get("summary") or ""), 900)
        if body:
            parts.append(f"[{kind}] {body}")
    user = (
        f"Предмет: {subject}\n"
        f"Сумма: {base.get('amount_text')}\n"
        f"Этапы по тексту файлов: {', '.join(item['label'] for item in rule_works) or 'пока не видно'}\n\n"
        f"Куски из файлов:\n" + "\n".join(parts)
    )
    data = llm.chat_json(_SYS_LEAD, user)
    if not data:
        works = _merge_works(rule_works, [])
        base["works"] = works
        base["why"] = _why_from_works(works, object_line=subject, llm_why=str(base.get("why") or ""))
        return base
    verdict = str(data.get("verdict") or base.get("verdict") or "maybe")
    if verdict not in {"yes", "no", "maybe"}:
        verdict = "maybe"
    works_raw = data.get("works") if isinstance(data.get("works"), list) else []
    llm_works = []
    for item in works_raw[:8]:
        if not isinstance(item, dict):
            continue
        profile = str(item.get("profile") or "")
        llm_works.append(
            {
                "profile": profile,
                "label": str(item.get("label") or PROFILE_LABELS.get(profile, profile)),
                "evidence": _clip(str(item.get("evidence") or ""), 160),
            }
        )
    works = _merge_works(rule_works, llm_works)
    if any(item.get("profile") in _CORE for item in works):
        verdict = "yes"
    minus = data.get("minus") if isinstance(data.get("minus"), list) else base.get("minus") or []
    object_line = str(data.get("label") or subject or "")
    if re.search(r"нежил|объект", object_line, re.I) and len(object_line) < 50:
        named = re.search(
            r"(центр(?:а)? социальной реабилитации[^.]{0,60})",
            blob or subject,
            re.I,
        )
        if named:
            object_line = _clip(_clean(named.group(1)), 140)
    return {
        "verdict": verdict,
        "label": _clip(object_line, 160),
        "why": _why_from_works(works, object_line=object_line, llm_why=str(data.get("why") or "")),
        "works": works or base.get("works") or [],
        "minus": [str(item) for item in minus if item][:8],
        "amount_text": base.get("amount_text"),
        "via": "llm",
    }


def _tidy_quote(chunk: str) -> str:
    text = _clean(chunk)
    cut = re.search(r"(.+?)\s+шт\b", text, re.I)
    if cut and len(cut.group(1)) >= 8:
        text = cut.group(1).strip(" .,:;")
    text = re.sub(r"\d[\d\s]{6,}", " ", text)
    return _clip(_clean(text), 90)


def _parse_money_values(text: str) -> list[float]:
    found: list[float] = []
    blob = (text or "").replace("\xa0", " ")
    for whole, cop in _MONEY.findall(blob):
        digits = re.sub(r"\D", "", whole)
        if not digits:
            continue
        value = int(digits) + int(cop) / 100
        if value >= 100:
            found.append(value)
    return found


_NAMED_QUOTE = re.compile(r"«([^»]{6,90})»\s*([\d\s\u00a0]{3,24},\d{2})")
_SECTION_TOTAL = re.compile(
    r"итого по разделу\s*\d*\.?\s*(.{3,70}?)\s+([\d\s\u00a0]+,\d{2})(?:\s+([\d\s\u00a0]+,\d{2}))?",
    re.I,
)
_COST_LINE = re.compile(
    r"сметная стоимость\s+([\d\s\u00a0]+,\d{2})(.{0,48})",
    re.I,
)
_OUR_SMETA = (
    "геодез",
    "разбивк",
    "землян",
    "котлован",
    "нулевой",
    "фундамент",
    "свай",
    "ростверк",
    "монолит",
    "бетон",
    "опалуб",
)
_SKIP_SMETA_TITLE = (
    "наименование",
    "видов работ",
    "файла сметы",
    "общая стоимость по смете",
)


def _short_smeta_title(raw: str) -> str:
    text = _clean(raw)
    text = re.sub(r"смета без сводного сметного расчета\s*\(сср\)", "", text, flags=re.I)
    text = text.strip(" «»\"'-")
    if " - " in text:
        parts = [
            part.strip()
            for part in text.split(" - ")
            if part.strip() and part.strip().upper() not in {"ЛСР", "СМЕТА"}
        ]
        if parts:
            text = parts[-1]
    text = re.sub(r"^итого по разделу\s*\d*\.?\s*", "", text, flags=re.I)
    return _clip(text, 42) or "смета"


def _is_our_smeta_title(title: str) -> bool:
    folded = _fold(title)
    return any(bit in folded for bit in _OUR_SMETA)


def _smeta_named_totals(text: str) -> list[dict[str, str]]:
    blob = text or ""
    found: list[dict[str, str]] = []
    seen: set[int] = set()

    def add(title: str, amount: float) -> None:
        if amount < 1000:
            return
        key = int(round(amount))
        if key in seen:
            return
        short = _short_smeta_title(title)
        folded = _fold(short)
        if any(bit in folded for bit in _SKIP_SMETA_TITLE):
            return
        seen.add(key)
        found.append(
            {
                "title": short,
                "amount": str(key),
                "amount_text": money(key),
            }
        )

    for match in _NAMED_QUOTE.finditer(blob):
        amounts = _parse_money_values(match.group(2))
        if amounts:
            add(match.group(1), amounts[-1])
    for match in _SECTION_TOTAL.finditer(blob):
        amounts = _parse_money_values(" ".join(part for part in match.groups()[1:] if part))
        if amounts:
            add(match.group(1), max(amounts))
    for match in _COST_LINE.finditer(blob):
        amounts = _parse_money_values(match.group(1))
        if not amounts:
            continue
        value = amounts[0]
        tail = _fold(match.group(2) or "")
        if "тыс" in tail and value < 1_000_000:
            value *= 1000
        if "тыс" not in tail and value < 10_000:
            continue
        add("смета целиком", value)
    found.sort(key=lambda item: -float(item["amount"]))
    return found[:8]


def _smeta_line(items: list[dict[str, str]]) -> str:
    bits = [f"{item['title']} {item['amount_text']}" for item in items[:5]]
    return "В смете: " + "; ".join(bits) + "." if bits else ""


def _vat_total(amounts: list[float]) -> int | None:
    """В смете часто: сумма без НДС + НДС 20% = сумма с НДС."""
    n = min(len(amounts), 8)
    best: float | None = None
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            base, vat = amounts[i], amounts[j]
            if vat >= base or vat < base * 0.05 or vat > base * 0.25:
                continue
            total = base + vat
            if any(abs(item - total) <= 1.5 for item in amounts):
                if best is None or total > best:
                    best = total
    if best is None:
        return None
    return int(round(best))


def _stage_hit(folded: str, hint: str) -> dict[str, str] | None:
    start = 0
    best: tuple[int, str, int | None] | None = None
    while True:
        idx = folded.find(hint, start)
        if idx < 0:
            break
        window = folded[idx : idx + 320]
        amounts = _parse_money_values(window)
        estimate = "шт" in window and len(amounts) >= 3
        total = _vat_total(amounts) if estimate else None
        score = 0
        if estimate:
            score += 6
        if total:
            score += 12
        if re.search(r"\d{2}-\d{2}-\d{2}", window):
            score += 3
        if score > (best[0] if best else -1):
            best = (score, _tidy_quote(folded[idx : idx + 90]), total)
        start = idx + max(len(hint), 1)
        if start <= idx:
            break
    if best is None:
        return None
    _, evidence, total = best
    if not evidence:
        return None
    item: dict[str, str] = {"evidence": evidence}
    if total:
        item["amount"] = str(total)
        item["amount_text"] = money(total)
    return item


def work_hits(blob: str) -> list[dict[str, str]]:
    folded = _fold(blob)
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for name in _OUR_ORDER:
        hints = GROUP_HINTS.get(name) or ()
        best_item: dict[str, str] | None = None
        best_score = -1
        for hint in hints:
            hit = _stage_hit(folded, hint)
            if not hit:
                continue
            score = 20 if hit.get("amount_text") else 1
            if score > best_score:
                best_score = score
                best_item = hit
        if not best_item or name in seen:
            continue
        seen.add(name)
        found.append(
            {
                "profile": name,
                "label": PROFILE_LABELS.get(name, name),
                "evidence": best_item.get("evidence") or "",
                **({"amount": best_item["amount"], "amount_text": best_item["amount_text"]}
                if best_item.get("amount_text")
                else {}),
            }
        )
    return found


def _minus_hits(blob: str) -> list[str]:
    folded = _fold(blob)
    return [item for item in _MINUS if item in folded]


def _looks_like_archive(name: str) -> bool:
    folded = _fold(name or "")
    return folded.endswith(".zip") or "приложен" in folded or folded.endswith(".rar")


def _hit_ok(evidence: str, profile: str) -> bool:
    ev = _fold(evidence or "")
    if len(ev) < 6:
        return False
    if re.search(r"от=\d|эм=\d|отм=\d|сп\s*\d+\.\d+|снип|актуализирован", ev):
        return False
    if "эксплуатируем" in ev or "имеются инженерн" in ev:
        return False
    if profile == "box" and "пвх" in ev:
        return False
    if profile == "earth":
        strong = any(bit in ev for bit in ("земляные", "котлован", "нулевой", "выемк", "шпунт", "транше", "отсыпк"))
        if not strong and any(bit in ev for bit in ("водопровод", "тепловые сети", "электроснабжен", "телефонизац")):
            return False
    if profile == "monolith" and "основные положения" in ev:
        return False
    return True


def _work_row(item: dict[str, str]) -> dict[str, str]:
    evidence = _clean(str(item.get("evidence") or ""))
    if "." in evidence:
        head = evidence.split(".", 1)[0].strip()
        if 6 <= len(head) <= 80:
            evidence = head
    row = {
        "profile": item["profile"],
        "label": item.get("label") or PROFILE_LABELS.get(item["profile"], item["profile"]),
        "evidence": evidence,
    }
    if item.get("amount"):
        row["amount"] = str(item["amount"])
    if item.get("amount_text"):
        row["amount_text"] = str(item["amount_text"])
    return row


def _collect_hits(subject: str, documents: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_profile: dict[str, dict[str, str]] = {}

    def consider(item: dict[str, str]) -> None:
        key = item.get("profile") or ""
        if key == "object" or key not in PROFILE_LABELS:
            return
        if not _hit_ok(str(item.get("evidence") or ""), key):
            return
        prev = by_profile.get(key)
        new_score = 20 if item.get("amount_text") else 1
        if prev is None:
            by_profile[key] = item
            return
        prev_score = 20 if prev.get("amount_text") else 1
        if new_score > prev_score:
            by_profile[key] = item

    for doc in documents:
        text = str(doc.get("ocr_text") or "")
        if len(text.strip()) < 40:
            continue
        for hit in work_hits(text):
            consider(hit)
    for hit in work_hits(subject or ""):
        consider(hit)
    return [_work_row(by_profile[key]) for key in _OUR_ORDER if key in by_profile]


def _read_lines(documents: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for doc in documents:
        if str(doc.get("value") or "") != "read":
            continue
        text = _clean(str(doc.get("summary") or ""))
        if not text or text in seen:
            continue
        folded = _fold(text)
        if "открыть" in folded or "глазами" in folded:
            continue
        seen.add(text)
        lines.append(text)
    return lines


def _file_state(documents: list[dict[str, Any]]) -> dict[str, Any]:
    has_text = False
    unread_any = False
    unread_archive = False
    has_lsr = False
    for doc in documents:
        status = str(doc.get("ocr_status") or "")
        name = str(doc.get("filename") or "")
        kind = _fold(str(doc.get("kind") or ""))
        summary = _fold(str(doc.get("summary") or ""))
        if str(doc.get("ocr_text") or "").strip():
            has_text = True
        if status in {"pending", ""}:
            unread_any = True
        if status in {"empty", "pending"} and _looks_like_archive(name):
            unread_archive = True
        if kind == "смета" or "лср" in summary or "лср" in _fold(name) or "локальн" in summary:
            has_lsr = True
    return {
        "has_text": has_text,
        "unread_any": unread_any,
        "unread_archive": unread_archive,
        "has_lsr": has_lsr,
    }


def _source_note(
    state: dict[str, Any], hits: list[dict[str, str]], named: list[dict[str, str]]
) -> str:
    if any(item.get("amount_text") for item in hits) or named:
        return "Цифры из файлов."
    if state["has_lsr"]:
        return "Смета в файлах есть, цифр по этапам нет."
    if state["unread_archive"]:
        return "Архив приложений ещё не разобрали."
    if state["unread_any"] and not state["has_text"]:
        return "Файлы ещё не читали."
    if state["has_text"]:
        return "Смотрели файлы, отдельных цифр по этапам нет."
    return "Пока только предмет заказа."


def _why_from_facts(
    hits: list[dict[str, str]],
    named: list[dict[str, str]],
    read_lines: list[str],
    object_hit: bool,
) -> str:
    paid = [item for item in hits if item.get("amount_text") and item["profile"] in _CORE]
    if paid:
        bits = [f"{item['label']} {item['amount_text']}" for item in paid]
        why = "В смете: " + "; ".join(bits) + "."
        extra = [line for line in read_lines if "сам делает" in _fold(line) and "₽" in line]
        if extra:
            why += " " + extra[0]
        if object_hit:
            why += " Объект целиком — звоним победителю."
        return why
    if named:
        why = _smeta_line(named)
        if not any(_is_our_smeta_title(item["title"]) for item in named):
            why += " Земли, фундамента и монолита в этих строках нет."
        if object_hit:
            why += " Объект целиком — звоним победителю."
        return why
    useful = [line for line in read_lines if "₽" in line]
    if useful:
        why = " ".join(useful[:2])
        if object_hit:
            why += " Объект целиком — звоним победителю."
        return why
    bits = []
    for item in hits:
        if item["profile"] not in _CORE:
            continue
        if item.get("evidence"):
            bits.append(f"{item['label']} — «{item['evidence']}»")
        else:
            bits.append(item["label"])
    if bits:
        why = "В тексте названы " + "; ".join(bits[:3]) + ". Сумм рядом нет."
        if object_hit:
            why += " Объект целиком — звоним победителю."
        return why
    if object_hit:
        return "По предмету стройка или капремонт здания. В прочитанных файлах цифр по земле, фундаменту и монолиту нет."
    return "Ключи наших работ в тексте слабые."


def _why_object(state: dict[str, Any], read_lines: list[str]) -> str:
    if read_lines:
        why = " ".join(read_lines[:3])
        if "звон" not in _fold(why):
            why += " Объект целиком — звоним победителю."
        return why
    if state["has_lsr"]:
        return "По предмету стройка или капремонт здания. Смета в файлах есть, цифр по этапам нет."
    if state["unread_archive"]:
        return "По предмету стройка или капремонт здания. Смета, скорее всего, в архиве приложений — его ещё не разобрали."
    if state["unread_any"] and not state["has_text"]:
        return "По предмету стройка или капремонт здания. Файлы ещё не прочитали."
    if state["has_text"]:
        return "По предмету стройка или капремонт здания. В прочитанных файлах цифр по земле, фундаменту и монолиту нет."
    return "По предмету это стройка или капремонт здания."


def lead_analysis(
    *,
    subject: str,
    amount_text: str,
    profiles: list[str],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    doc_texts = [str(item.get("ocr_text") or "") for item in documents]
    blob = _fold(" ".join([subject or "", *doc_texts]))
    hits = _collect_hits(subject, documents)
    hit_keys = [item["profile"] for item in hits]
    minus = _minus_hits(blob)
    core = [k for k in hit_keys if k in _CORE]
    object_hit = "object" in profiles or any(
        hint in _fold(subject or "") for hint in GROUP_HINTS.get("object") or ()
    )
    box_hit = "box" in hit_keys
    state = _file_state(documents)
    named = _smeta_named_totals(" ".join(doc_texts))
    read_lines = _read_lines(documents)
    source = _source_note(state, hits, named)
    why_bits = _why_from_facts(hits, named, read_lines, object_hit)

    cadastral = any(item in minus for item in ("кадастр", "межеван", "генплан", "землеустрой"))
    supply = "поставка товара" in minus

    if cadastral and not core and not object_hit:
        verdict = "no"
        label = "Не наш"
        why = "В предмете или файлах кадастр / межевание / генплан. Это не стройка нулевого цикла."
    elif supply and not core and not object_hit:
        verdict = "no"
        label = "Не наш"
        why = "По документам это поставка товара, не строительные работы."
    elif core and any(item.get("amount_text") for item in hits if item["profile"] in _CORE):
        verdict = "yes"
        label = "Наш заход: " + ", ".join(PROFILE_LABELS.get(k, k) for k in core)
        why = why_bits
    elif object_hit:
        verdict = "yes"
        label = "Наш заход: стройка объекта"
        why = why_bits
    elif core:
        verdict = "yes"
        label = "Наш заход: " + ", ".join(PROFILE_LABELS.get(k, k) for k in core)
        why = why_bits
    elif box_hit:
        verdict = "maybe"
        label = "Слабо: коробка"
        why = "Есть металлокаркас / сэндвич / газобетон. Имеет смысл, если победитель сам не закрывает коробку."
    elif documents and not state["has_text"]:
        verdict = "maybe"
        label = "Пока по заголовку"
        why = "Файлы ещё не прочитали. Смотрите предмет заказа."
    else:
        verdict = "maybe"
        label = "Слабо, смотреть руками"
        why = "Ключи наших работ в тексте слабые. Не отбрасывайте сразу, если предмет похож на стройку."

    paid = any(item.get("amount_text") for item in hits if item["profile"] in _CORE)
    smeta = [] if paid else named

    return {
        "verdict": verdict,
        "label": label,
        "why": _clip(why, 520),
        "works": hits,
        "smeta": smeta,
        "minus": minus if verdict == "no" else [],
        "amount_text": amount_text,
        "via": "text",
        "source": source,
    }


def summary_to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def summary_from_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
