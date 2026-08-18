"""Реквизиты юрлица по ИНН. Телефоны этим методом не обещаем."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app import config, db
from app.present import MISSING, ORG_STATUS_RU

FIND_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


def _values(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("value") or item.get("source") or "").strip()
        else:
            text = ""
        if text and text not in out:
            out.append(text)
    return out


def _parse(payload: dict[str, Any]) -> dict[str, Any]:
    suggestions = payload.get("suggestions") or []
    if not suggestions:
        return {
            "name": MISSING,
            "status": "в справочнике нет",
            "status_code": "",
            "address": MISSING,
            "director": MISSING,
            "ogrn": MISSING,
            "phones": [],
            "emails": [],
        }
    first = suggestions[0] if isinstance(suggestions[0], dict) else {}
    data = first.get("data") or {}
    state = data.get("state") or {}
    code = str(state.get("status") or "")
    management = data.get("management") or {}
    name = data.get("name") or {}
    address = data.get("address") or {}
    director = management.get("name") or (data.get("fio") or {}).get("source")
    return {
        "name": str(name.get("short_with_opf") or first.get("value") or MISSING),
        "status": ORG_STATUS_RU.get(code, code.lower() if code else MISSING),
        "status_code": code,
        "address": str(address.get("unrestricted_value") or address.get("value") or MISSING),
        "director": str(director or MISSING),
        "ogrn": str(data.get("ogrn") or MISSING),
        "phones": _values(data.get("phones") or data.get("phone")),
        "emails": _values(data.get("emails") or data.get("email")),
    }


def cached(conn, inn: str) -> dict[str, Any] | None:
    row = db.get_org(conn, inn, "dadata")
    if row is None or not row["payload"]:
        return None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None
    parsed = _parse(payload) if "suggestions" in payload else payload
    parsed.setdefault("phones", [])
    parsed.setdefault("emails", [])
    if row["status"]:
        parsed["status"] = ORG_STATUS_RU.get(row["status"], parsed.get("status") or row["status"])
        parsed["status_code"] = row["status"]
    if row["name"]:
        parsed["name"] = row["name"]
    if row["ogrn"]:
        parsed["ogrn"] = row["ogrn"]
    return parsed


def fetch_and_store(conn, inn: str) -> dict[str, Any] | None:
    if not config.DADATA_API_KEY:
        return None
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {config.DADATA_API_KEY}",
    }
    if config.DADATA_SECRET_KEY:
        headers["X-Secret"] = config.DADATA_SECRET_KEY
    try:
        with httpx.Client(timeout=8.0, trust_env=False) as client:
            response = client.post(FIND_URL, headers=headers, json={"query": inn})
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    parsed = _parse(payload)
    db.upsert_org_cache(
        conn,
        inn,
        name=parsed.get("name") if parsed.get("name") != MISSING else None,
        status=parsed.get("status_code") or None,
        ogrn=parsed.get("ogrn") if parsed.get("ogrn") != MISSING else None,
        payload=json.dumps(payload, ensure_ascii=False),
        source="dadata",
    )
    conn.commit()
    return parsed
