"""Сайт и контакты компании по ИНН через официальный API Checko."""

from __future__ import annotations

import json
import re
from typing import Any

from app import config
from app.htmlutil import normalize_email, normalize_phone, normalize_website, strip_tags
from app.http_client import FetchError, Http
from app.inncheck import normalize_inn

SEARCH = "https://checko.ru/search"
API_COMPANY = "https://api.checko.ru/v2/company"
API_IP = "https://api.checko.ru/v2/entrepreneur"
_SITE = re.compile(r"Веб-сайт\s+(\S+)", re.I)


def parse_website(html: str) -> str | None:
    text = strip_tags(html or "")
    match = _SITE.search(text)
    if not match:
        return None
    return normalize_website(match.group(1))


def _norm_phone(raw: str) -> str | None:
    return normalize_phone(raw)


def _contacts_from_api(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        data = payload
    contacts = data.get("Контакты") if isinstance(data, dict) else None
    if not isinstance(contacts, dict):
        contacts = {}
    raw_site = contacts.get("ВебСайт") or contacts.get("Сайт")
    if isinstance(raw_site, list):
        raw_site = raw_site[0] if raw_site else None
    phones: list[str] = []
    for item in contacts.get("Тел") or []:
        if not isinstance(item, str):
            continue
        phone = _norm_phone(item)
        if phone and phone not in phones:
            phones.append(phone)
    raw_emails = contacts.get("Емэйл") or contacts.get("Email") or []
    if isinstance(raw_emails, str):
        raw_emails = [raw_emails]
    emails: list[str] = []
    for item in raw_emails:
        if isinstance(item, dict):
            item = item.get("Емэйл") or item.get("value") or item.get("email") or ""
        if not isinstance(item, str):
            continue
        mail = normalize_email(item)
        if mail and mail not in emails:
            emails.append(mail)
    return {
        "website": normalize_website(str(raw_site)) if raw_site else None,
        "phones": phones,
        "emails": emails,
    }


def profile(http: Http, inn: str) -> dict | None:
    clean = normalize_inn(inn)
    if not clean:
        return None
    if config.CHECKO_API_KEY:
        return _profile_api(http, clean)
    return _profile_html(http, clean)


def _profile_api(http: Http, inn: str) -> dict | None:
    url = API_IP if len(inn) == 12 else API_COMPANY
    try:
        response = http.get(url, params={"key": config.CHECKO_API_KEY, "inn": inn})
    except FetchError:
        return None
    if response.status_code in {403, 429}:
        return {"inn": inn, "website": None, "phones": [], "emails": [], "error": "rate_limit"}
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    parsed = _contacts_from_api(payload)
    return {"inn": inn, **parsed}


def _profile_html(http: Http, inn: str) -> dict | None:
    try:
        response = http.get(SEARCH, params={"query": inn})
    except FetchError:
        return None
    if response.status_code in {403, 429}:
        return {"inn": inn, "website": None, "phones": [], "emails": [], "error": "rate_limit"}
    if response.status_code >= 400:
        return None
    if "подтвердите, что вы человек" in response.text.lower():
        return {"inn": inn, "website": None, "phones": [], "emails": [], "error": "rate_limit"}
    return {
        "inn": inn,
        "website": parse_website(response.text),
        "phones": [],
        "emails": [],
    }


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
