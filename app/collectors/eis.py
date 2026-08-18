"""Реестр контрактов ЕИС 44-ФЗ, регион КЛАДР области."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from app import config
from app.htmlutil import (
    abs_url,
    emails,
    parse_amount_rub,
    parse_ru_date,
    phones,
    site_from_email,
    strip_tags,
    labeled_websites,
)
from app.http_client import FetchError, Http
from app.inncheck import normalize_inn
from app.models import AttachmentDraft, ContactDraft, ListingHit, LotDraft

BASE = config.EIS_BASE
SEARCH = f"{BASE}/epz/contract/search/results.html"
CARD = f"{BASE}/epz/contract/contractCard/common-info.html"
PARTS = f"{BASE}/epz/contract/contractCard/participants.html"
DOCS = f"{BASE}/epz/contract/contractCard/document-info.html"

_REESTR = re.compile(r"reestrNumber=(\d{15,25})")
_INN_QS = re.compile(r"[?&]inn=(\d{10,12})\b", re.I)
_TITLE_ATTR = re.compile(r'title="([^"]+)"')
_TOOLTIP = re.compile(r"data-tooltip='([^']+)'")
_FILESTORE = re.compile(
    r'href="([^"]*filestore[^"]*)"[^>]*>([^<]{0,240})',
    re.I,
)


def listing_url(
    eis: dict[str, Any],
    query: str,
    page: int,
    records: str,
    *,
    date_from_ru: str | None = None,
    date_to_ru: str | None = None,
    price_from: int | None = None,
) -> str:
    params = {
        "morphology": str(eis.get("morphology") or "off"),
        "search-filter": "Дате размещения",
        "fz44": "on",
        "contractStageList_0": "on",
        "contractStageList_1": "on",
        "contractStageList": "0,1",
        "customerPlace": str(eis.get("customer_place") or "77000000000"),
        "customerPlaceCodes": str(eis.get("customer_place_codes") or "77"),
        "sortBy": "UPDATE_DATE",
        "pageNumber": str(page),
        "recordsPerPage": records,
        "showLotsInfoHidden": "false",
    }
    if eis.get("customer_place_nested", True):
        params["customerPlaceWithNested"] = "on"
    if query:
        params["searchString"] = query
    if date_from_ru:
        params["publishDateFrom"] = date_from_ru
    if date_to_ru:
        params["publishDateTo"] = date_to_ru
    if price_from and price_from > 0:
        params["contractPriceFrom"] = str(int(price_from))
    return SEARCH + "?" + urlencode(params, encoding="utf-8")


def parse_listing(html: str) -> list[ListingHit]:
    hits: list[ListingHit] = []
    seen: set[str] = set()
    for part in html.split("search-registry-entry-block")[1:]:
        match = _REESTR.search(part)
        if not match:
            continue
        reestr = match.group(1)
        if reestr in seen:
            continue
        seen.add(reestr)
        text = strip_tags(part)
        subject = _subject(part, text)
        customer = _between(text, "Заказчик", ("Контракт", "Объекты закупки", "Цена контракта"), 180)
        inn = None
        inn_m = _INN_QS.search(part)
        if inn_m:
            inn = normalize_inn(inn_m.group(1))
        amount = None
        price_m = re.search(r"Цена контракта\s+(.+?)(?:Заключение|Срок|Размещен|$)", text)
        if price_m:
            amount = parse_amount_rub(price_m.group(1))
        signed = None
        date_m = re.search(r"Заключение контракта\s+(\d{2}\.\d{2}\.\d{4})", text)
        if date_m:
            signed = parse_ru_date(date_m.group(1))
        published = None
        pub_m = re.search(
            r"Размещен контракт в реестре контрактов\s+(\d{2}\.\d{2}\.\d{4})", text
        )
        if pub_m:
            published = parse_ru_date(pub_m.group(1))
        hits.append(
            ListingHit(
                external_id=reestr,
                source="eis",
                url=f"{CARD}?reestrNumber={reestr}",
                subject=subject,
                amount_rub=amount,
                customer_name=customer,
                customer_inn=inn,
                signed_at=signed,
                published_at=published,
                fz="44",
            )
        )
    return hits


def _subject(part: str, text: str) -> str:
    for tooltip in _TOOLTIP.findall(part):
        cleaned = strip_tags(tooltip)
        low = cleaned.lower()
        if "предусмотрено формирование" in low or "электронной форме" in low:
            continue
        if len(cleaned) > 20:
            return cleaned[:2000]
    match = re.search(
        r"Объекты закупки\s+(.+?)(?:Цена контракта|Заключение контракта|Контракт №|$)",
        text,
    )
    if match:
        value = match.group(1).strip()
        value = re.sub(r"^Для контракта.*?(Объекты закупки\s+)?", "", value)
        if len(value) > 8:
            value = re.sub(r"\s*Посмотреть все.*$", "", value).strip()
            return value[:2000]
    return ""


def _between(text: str, title: str, stops: tuple[str, ...], limit: int) -> str:
    stop = "|".join(re.escape(item) for item in stops)
    match = re.search(rf"{re.escape(title)}\s+(.+?)(?:{stop}|$)", text)
    if not match:
        return ""
    return match.group(1).strip()[:limit]




def parse_card(html: str, hit: ListingHit) -> LotDraft:
    text = strip_tags(html)
    customer_name = hit.customer_name
    title_m = re.search(
        r'cardMainInfo__title">Заказчик</span>\s*<span class="cardMainInfo__content[^"]*">(.*?)</span>',
        html,
        re.S,
    )
    if title_m:
        inner = title_m.group(1)
        named = _TITLE_ATTR.search(inner)
        customer_name = strip_tags(named.group(1) if named else inner)[:300]
    inn = hit.customer_inn
    inn_block = re.search(
        r'section__title">ИНН</span>\s*<span class="section__info">(\d{10,12})</span>',
        html,
        re.S,
    )
    if inn_block:
        inn = normalize_inn(inn_block.group(1)) or inn
    if not inn:
        qs = _INN_QS.search(html)
        if qs:
            inn = normalize_inn(qs.group(1))
    amount = hit.amount_rub
    cost = re.search(r'class="cardMainInfo__content cost"[^>]*>(.*?)</span>', html, re.S)
    if cost:
        amount = parse_amount_rub(strip_tags(cost.group(1))) or amount
    subject = hit.subject
    obj = re.search(
        r"Объекты закупки</div>\s*<div class=\"cardMainInfo__content[^>]*>(.*?)</div>",
        html,
        re.S,
    )
    if obj:
        subj = strip_tags(obj.group(1))
        subj = re.sub(r"Посмотреть все.*$", "", subj).strip()
        if len(subj) > 8:
            subject = subj[:2000]
    if len(subject) < 8:
        fallback = re.search(r"Объекты закупки\s+(.+?)(?:Цена контракта|Посмотреть все|$)", text)
        if fallback:
            subject = fallback.group(1).strip()[:2000]
    signed = hit.signed_at
    sign_m = re.search(r"Заключение контракта\s+(\d{2}\.\d{2}\.\d{4})", text)
    if sign_m:
        signed = parse_ru_date(sign_m.group(1)) or signed
    draft = LotDraft(
        external_id=hit.external_id,
        source="eis",
        url=hit.url,
        subject=subject,
        amount_rub=amount,
        published_at=hit.published_at,
        signed_at=signed,
        fz=hit.fz or "44",
        customer_name=customer_name,
        customer_inn=inn,
    )
    _add_contacts(draft, html, text, party="customer", source="eis_card")
    return draft


def _add_contact(
    draft: LotDraft,
    *,
    value: str,
    type: str,
    party: str,
    source: str,
    snippet: str = "",
    confidence: float = 0.85,
) -> None:
    if not value:
        return
    seen = {(item.type, item.value.lower()) for item in draft.contacts}
    if (type, value.lower()) in seen:
        return
    draft.contacts.append(
        ContactDraft(
            value=value,
            type=type,
            party=party,
            source=source,
            confidence=confidence,
            snippet=snippet[:400],
        )
    )


def _add_contacts(draft: LotDraft, html: str, text: str, *, party: str, source: str) -> None:
    snippet = text[:400]
    for phone in phones(text):
        _add_contact(
            draft,
            value=phone,
            type="phone",
            party=party,
            source=source,
            snippet=snippet,
            confidence=0.9,
        )
    for mail in emails(text):
        _add_contact(
            draft,
            value=mail,
            type="email",
            party=party,
            source=source,
            snippet=snippet,
            confidence=0.9,
        )
        site = site_from_email(mail)
        if site:
            _add_contact(
                draft,
                value=site,
                type="website",
                party=party,
                source="email_domain",
                snippet=mail,
                confidence=0.7,
            )
    for site in labeled_websites(text):
        _add_contact(
            draft,
            value=site,
            type="website",
            party=party,
            source=source,
            snippet=snippet,
            confidence=0.8,
        )


def parse_participants(html: str, draft: LotDraft) -> LotDraft:
    text = strip_tags(html)
    inn_m = re.search(r"ИНН:\s*(\d{10,12})", text)
    if inn_m:
        draft.winner_inn = normalize_inn(inn_m.group(1))
    name_m = re.search(
        r"tableBlock__col_first[^>]*>(.*?)</td>",
        html,
        re.S,
    )
    if name_m:
        name = strip_tags(name_m.group(1))
        name = re.split(r"Код по ОКПО|ИНН:", name)[0].strip()
        if name:
            draft.winner_name = name[:400]
    _add_contacts(draft, html, text, party="winner", source="eis_participants")
    return draft


def parse_documents(html: str) -> list[AttachmentDraft]:
    out: list[AttachmentDraft] = []
    seen: set[str] = set()
    for href, label in _FILESTORE.findall(html):
        url = abs_url(BASE, href)
        if "/44fz/" not in url and "/filestore/" in url:
            url = url.replace("/filestore/", "/44fz/filestore/", 1)
        if url in seen:
            continue
        seen.add(url)
        name = strip_tags(label).strip() or href.rsplit("/", 1)[-1]
        name = name[:200] or "файл"
        out.append(AttachmentDraft(url=url, filename=name))
        if len(out) >= 40:
            break
    return out


class EisCollector:
    name = "eis"

    def __init__(
        self,
        http: Http,
        eis_cfg: dict[str, Any],
        records: str,
        *,
        date_from_ru: str | None = None,
        date_to_ru: str | None = None,
        price_from: int | None = None,
    ) -> None:
        self.http = http
        self.eis_cfg = eis_cfg
        self.records = records
        self.date_from_ru = date_from_ru
        self.date_to_ru = date_to_ru
        self.price_from = price_from

    def fetch_listing(self, query: str, page: int) -> list[ListingHit]:
        url = listing_url(
            self.eis_cfg,
            query,
            page,
            self.records,
            date_from_ru=self.date_from_ru,
            date_to_ru=self.date_to_ru,
            price_from=self.price_from,
        )
        html = self.http.get_text(url)
        if "search-registry-entry-block" not in html and len(html) < 500:
            raise FetchError(f"пустая лента ЕИС page={page} q={query!r}")
        return parse_listing(html)

    def fetch_card(self, hit: ListingHit) -> LotDraft | None:
        html = self.http.get_text(CARD, params={"reestrNumber": hit.external_id})
        draft = parse_card(html, hit)
        try:
            parts = self.http.get_text(PARTS, params={"reestrNumber": hit.external_id})
            parse_participants(parts, draft)
        except FetchError:
            pass
        return draft

    def fetch_attachments(self, hit: ListingHit) -> list[AttachmentDraft]:
        try:
            html = self.http.get_text(DOCS, params={"reestrNumber": hit.external_id})
        except FetchError:
            return []
        return parse_documents(html)
