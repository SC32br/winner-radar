"""mos.ru JSON. Регион в API не фильтрует — режем сами. Без победителя лот не берём."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from app.htmlutil import parse_ru_date
from app.http_client import FetchError, Http
from app.inncheck import normalize_inn
from app.models import AttachmentDraft, ListingHit, LotDraft
from app.prefilter import _fold, geo_ok

QUERY_URL = "https://old.zakupki.mos.ru/api/Cssp/Purchase/Query"
DETAIL_URL = "https://zakupki.mos.ru/newapi/api/Auction/Get"
FILE_URL = "https://zakupki.mos.ru/newapi/api/FileStorage/Download?id={id}"

_DEAD = (
    "отменен",
    "не состоял",
    "снята с публ",
    "запланир",
)


def _winner_name(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()[:400]
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("supplierName") or "").strip()[:400]
    return str(raw)[:400]


def _winner_inn(raw: Any) -> str | None:
    if isinstance(raw, dict):
        return normalize_inn(str(raw.get("inn") or raw.get("supplierInn") or ""))
    return None


def _customers(item: dict[str, Any]) -> tuple[str, str | None]:
    rows = item.get("customers") or []
    if not rows:
        return "", None
    first = rows[0] if isinstance(rows[0], dict) else {}
    name = str(first.get("name") or "")[:300]
    inn = normalize_inn(str(first.get("inn") or ""))
    return name, inn


def _money(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return int(value)


def _amount(item: dict[str, Any]) -> int | None:
    return _money(item.get("startPrice") if item.get("startPrice") is not None else item.get("price"))


def _eis_id(external_url: str) -> str | None:
    if not external_url:
        return None
    for key in ("reestrNumber=", "regNumber="):
        if key in external_url:
            tail = external_url.split(key, 1)[1]
            digits = "".join(ch for ch in tail if ch.isdigit())
            if len(digits) >= 11:
                return digits
    return None


def _dead_state(name: str) -> bool:
    blob = _fold(name)
    return any(token in blob for token in _DEAD)


def parse_items(payload: Any, geo_words: list[str]) -> list[ListingHit]:
    if isinstance(payload, dict):
        rows = payload.get("items") or payload.get("data") or payload.get("result") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    hits: list[ListingHit] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        if _dead_state(str(item.get("stateName") or "")):
            continue
        winner = item.get("winner")
        winner_name = _winner_name(winner)
        winner_inn = _winner_inn(winner)
        if not winner_name or not winner_inn:
            continue
        amount = _amount(item)
        if amount is None:
            continue
        region = str(item.get("regionName") or "").strip()
        name = str(item.get("name") or "")
        customer_name, customer_inn = _customers(item)
        blob = " ".join([region, name, customer_name])
        if not geo_ok(blob, geo_words):
            continue
        number = str(item.get("number") or item.get("id") or "").strip()
        if not number:
            continue
        ext_url = str(item.get("externalUrl") or "")
        eis_id = _eis_id(ext_url)
        external_id = eis_id or f"mos:{number}"
        source = "eis,mos" if eis_id else "mos"
        url = ext_url or f"https://zakupki.mos.ru/purchase/{number}"
        law = str(item.get("federalLawName") or "")
        fz = "44" if "44" in law else ("223" if "223" in law else "")
        auction_id = item.get("auctionId")
        hits.append(
            ListingHit(
                external_id=external_id,
                source=source,
                url=url,
                subject=name[:2000],
                amount_rub=amount,
                customer_name=customer_name,
                customer_inn=customer_inn,
                signed_at=parse_ru_date(str(item.get("endDate") or item.get("beginDate") or "")),
                published_at=parse_ru_date(str(item.get("beginDate") or item.get("endDate") or "")),
                fz=fz,
                extra={
                    "winner_name": winner_name,
                    "winner_inn": winner_inn,
                    "region_name": region,
                    "mos_number": number,
                    "auction_id": auction_id,
                    "state_name": str(item.get("stateName") or ""),
                },
            )
        )
    return hits


def _files_from_detail(payload: dict[str, Any]) -> list[AttachmentDraft]:
    out: list[AttachmentDraft] = []
    seen: set[str] = set()
    for row in payload.get("files") or []:
        if not isinstance(row, dict):
            continue
        file_id = row.get("id")
        if file_id is None:
            continue
        url = FILE_URL.format(id=file_id)
        if url in seen:
            continue
        seen.add(url)
        filename = str(row.get("name") or f"file-{file_id}")[:240]
        out.append(AttachmentDraft(url=url, filename=filename))
    return out


def _detail_winner(payload: dict[str, Any]) -> tuple[str, str | None]:
    for key in ("lastBetSupplier", "winner"):
        raw = payload.get(key)
        name = _winner_name(raw)
        inn = _winner_inn(raw)
        if name:
            return name, inn
    return "", None


class MosCollector:
    name = "mos"

    def __init__(self, http: Http, geo_words: list[str]) -> None:
        self.http = http
        self.geo_words = geo_words

    def fetch_listing(self, query: str, page: int) -> list[ListingHit]:
        take = 50
        skip = max(0, (page - 1) * take)
        dto = {
            "filter": {"nameLike": {"value": query}},
            "order": [{"field": "relevance", "desc": True}],
            "withCount": True,
            "take": take,
            "skip": skip,
        }
        encoded = quote(json.dumps(dto, ensure_ascii=False), safe="")
        url = f"{QUERY_URL}?queryDto={encoded}"
        try:
            payload = self.http.get_json(url)
        except FetchError:
            return []
        return parse_items(payload, self.geo_words)

    def fetch_card(self, hit: ListingHit) -> LotDraft | None:
        extra = dict(hit.extra or {})
        winner_name = str(extra.get("winner_name") or "")
        winner_inn = extra.get("winner_inn")
        amount = hit.amount_rub
        attachments: list[AttachmentDraft] = []
        auction_id = extra.get("auction_id")
        if auction_id:
            try:
                detail = self.http.get_json(f"{DETAIL_URL}?auctionId={auction_id}")
            except FetchError:
                detail = None
            if isinstance(detail, dict):
                amount = (
                    _money(detail.get("contractCost"))
                    or _money(detail.get("startCost"))
                    or _money(detail.get("lastBetCost"))
                    or amount
                )
                name, inn = _detail_winner(detail)
                if name and not winner_name:
                    winner_name = name
                if inn and not winner_inn:
                    winner_inn = inn
                attachments = _files_from_detail(detail)
                state = detail.get("state")
                if isinstance(state, dict) and _dead_state(str(state.get("name") or "")):
                    return None
        if not winner_name or not winner_inn or amount is None:
            return None
        return LotDraft(
            external_id=hit.external_id,
            source=hit.source,
            url=hit.url,
            subject=hit.subject,
            amount_rub=amount,
            region_text=str(extra.get("region_name") or ""),
            published_at=hit.published_at,
            signed_at=hit.signed_at,
            fz=hit.fz,
            customer_name=hit.customer_name,
            customer_inn=hit.customer_inn,
            winner_name=winner_name,
            winner_inn=winner_inn,
            attachments=attachments,
        )

    def fetch_attachments(self, hit: ListingHit) -> list[AttachmentDraft]:
        draft = self.fetch_card(hit)
        return list(draft.attachments) if draft else []
