"""Профиль по ИНН, не дискавери. Ходить напрямую, без прокси ЕИС."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from app.http_client import FetchError, Http
from app.inncheck import normalize_inn

SEARCH = "https://openapi.clearspending.ru/restapi/v3/contracts/search/"


def profile(http: Http, inn: str) -> dict | None:
    clean = normalize_inn(inn)
    if not clean:
        return None
    query = urlencode({"supplierinn": clean, "perpage": "5", "page": "1"})
    url = SEARCH + "?" + query
    try:
        payload = http.get_json(url)
    except FetchError:
        return None
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    contracts = payload.get("contracts")
    total = None
    items: list = []
    if isinstance(contracts, dict):
        total = contracts.get("total")
        raw_items = contracts.get("data") or contracts.get("items") or []
        items = raw_items if isinstance(raw_items, list) else []
    elif isinstance(contracts, list):
        items = contracts
        total = payload.get("total")
    if total is None:
        total = payload.get("total")
    sample = []
    if isinstance(items, list):
        for row in items[:3]:
            if not isinstance(row, dict):
                continue
            sample.append(
                {
                    "price": row.get("price"),
                    "signDate": row.get("signDate") or row.get("publishDate"),
                    "product": ((row.get("products") or [{}])[0] or {}).get("name")
                    if isinstance(row.get("products"), list)
                    else None,
                }
            )
    return {
        "inn": clean,
        "total": total,
        "sample": sample,
        "raw_keys": sorted(payload.keys())[:20],
    }


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
