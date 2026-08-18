"""Сайт победителя из почты: suar-grupp@mail.ru → suar-grupp.ru, снять телефоны."""

from __future__ import annotations

import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

from app.htmlutil import (
    email_site_candidates,
    emails,
    is_free_mail,
    is_junk_website,
    normalize_website,
    phones_loose,
    strip_tags,
)
from app.http_client import FetchError, Http

_PARK_HINTS = (
    "домен прода",
    "domain for sale",
    "this domain is",
    "парковка домена",
    "домен не привязан",
    "сайт ещё не создан",
    "coming soon",
    "parked domain",
)
_PARK_HOSTS = (
    "reg.ru",
    "nic.ru",
    "timeweb.ru",
    "beget.com",
    "godaddy.com",
    "umi.ru",
    "umi-cms.ru",
)
_CONTACT_PATHS = ("/kontakty/", "/contacts/")
_CONTACT_HREF = re.compile(
    r"""href=["']([^"']*(?:kontakt|contact|svyaz|rekvizit|o-kompanii|about)[^"']*)["']""",
    re.I,
)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _same_site(guessed: str, final: str) -> bool:
    left = _host(guessed)
    right = _host(final)
    if not left or not right:
        return False
    return left == right or right.endswith("." + left)


def _is_parked(url: str, html: str, text: str) -> bool:
    host = _host(url)
    if any(host == item or host.endswith("." + item) for item in _PARK_HOSTS):
        return True
    blob = f"{html[:2000]}\n{text[:2000]}".lower()
    return any(hint in blob for hint in _PARK_HINTS)


def _request_url(url: str) -> str:
    host = _host(url)
    if not host:
        return url
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return url
    parsed = urlparse(url)
    netloc = ascii_host
    if parsed.port:
        netloc = f"{ascii_host}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


def _host_resolves(url: str) -> bool:
    host = _host(url)
    if not host:
        return False
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        ascii_host = host
    try:
        socket.setdefaulttimeout(1.5)
        return bool(socket.getaddrinfo(ascii_host, 443, proto=socket.IPPROTO_TCP))
    except OSError:
        return False


def _get_html(client: Http, url: str, fallback: Http | None = None) -> tuple[str, str] | None:
    if not _host_resolves(url):
        return None
    response = None
    try:
        response = client.get(_request_url(url), retries=1)
    except FetchError:
        if fallback is not None:
            try:
                response = fallback.get(_request_url(url), retries=1)
            except FetchError:
                return None
        else:
            return None
    if response is None or response.status_code >= 400:
        return None
    ctype = (response.headers.get("content-type") or "").lower()
    if "html" not in ctype and "text" not in ctype and ctype:
        return None
    final = str(response.url)
    if is_junk_website(final) or not _same_site(url, final):
        return None
    html = response.text or ""
    if len(html) < 80:
        return None
    return final, html


def _contacts_from_html(html: str) -> tuple[list[str], list[str]]:
    text = strip_tags(html)
    skip_mail = {"umi.ru", "umi-cms.ru", "wixpress.com", "sentry.io"}
    mails: list[str] = []
    for item in emails(text):
        domain = item.rsplit("@", 1)[-1]
        if any(domain == bad or domain.endswith("." + bad) for bad in skip_mail):
            continue
        mails.append(item)
    return phones_loose(text), mails


def _contact_urls(base: str, html: str) -> list[str]:
    found: list[str] = []
    root = base if base.endswith("/") else base + "/"
    for path in _CONTACT_PATHS:
        found.append(urljoin(root, path.lstrip("/")))
    for href in _CONTACT_HREF.findall(html):
        abs_url = urljoin(root, href.strip())
        if _same_site(base, abs_url):
            found.append(abs_url.split("#", 1)[0])
    out: list[str] = []
    for item in found:
        if item.rstrip("/") != base.rstrip("/") and item not in out:
            out.append(item)
    return out[:4]


_STOP_NAME = frozenset(
    {
        "общество",
        "ограниченной",
        "ответственностью",
        "товарищество",
        "акционерное",
        "публичное",
        "индивидуальный",
        "предприниматель",
        "компания",
        "групп",
        "группа",
        "строительная",
        "строительный",
    }
)


def _name_needles(name: str) -> list[str]:
    folded = (name or "").lower().replace("ё", "е")
    quoted = re.findall(r"[«\"']([^»\"']{3,40})[»\"']", folded)
    needles: list[str] = []
    for item in quoted:
        clean = re.sub(r"[^а-яa-z0-9-]+", " ", item).strip()
        if len(clean) >= 3:
            needles.append(clean)
            for part in clean.replace("-", " ").split():
                if len(part) >= 4 and part not in _STOP_NAME:
                    needles.append(part)
    for token in re.findall(r"[а-яa-z0-9-]{4,}", folded):
        if token in _STOP_NAME:
            continue
        needles.append(token)
        for part in token.split("-"):
            if len(part) >= 4 and part not in _STOP_NAME:
                needles.append(part)
    out: list[str] = []
    for item in needles:
        if item not in out:
            out.append(item)
    return out[:8]


def _is_their_site(text: str, html: str, inn: str, name: str) -> bool:
    blob = f"{text}\n{html}".lower().replace("ё", "е")
    digits = re.sub(r"\D", "", blob)
    if inn and inn in digits:
        return True
    return any(needle in blob for needle in _name_needles(name))


def _merge_contacts(
    phones: list[str], mails: list[str], extra_phones: list[str], extra_mails: list[str]
) -> None:
    for phone in extra_phones:
        if phone not in phones:
            phones.append(phone)
    for mail in extra_mails:
        if mail not in mails:
            mails.append(mail)


def scrape_verified_site(
    client: Http,
    url: str,
    fallback: Http | None = None,
    inn: str = "",
    name: str = "",
) -> dict[str, Any] | None:
    home = _get_html(client, url, fallback)
    if home is None:
        return None
    final, html = home
    text = strip_tags(html)
    if _is_parked(final, html, text):
        return None
    phones, mails = _contacts_from_html(html)
    pages_text = [text]
    pages_html = [html]
    for extra in _contact_urls(final, html):
        page = _get_html(client, extra, fallback)
        if page is None:
            continue
        extra_html = page[1]
        extra_text = strip_tags(extra_html)
        if _is_parked(page[0], extra_html, extra_text):
            continue
        pages_text.append(extra_text)
        pages_html.append(extra_html)
        more_phones, more_mails = _contacts_from_html(extra_html)
        _merge_contacts(phones, mails, more_phones, more_mails)
    blob_text = "\n".join(pages_text)
    blob_html = "\n".join(pages_html)
    if (inn or name) and not _is_their_site(blob_text, blob_html, inn, name):
        return None
    site = normalize_website(final) or url
    if not site:
        return None
    return {"website": site, "phones": phones, "emails": mails}


def probe_winner_site(
    client: Http,
    email: str,
    fallback: Http | None = None,
    inn: str = "",
    name: str = "",
) -> dict[str, Any] | None:
    from_free = is_free_mail(email.rsplit("@", 1)[-1]) if "@" in email else False
    for candidate in email_site_candidates(email):
        hit = scrape_verified_site(client, candidate, fallback, inn=inn, name=name)
        if hit is None:
            continue
        if from_free and not hit.get("phones") and not hit.get("emails"):
            continue
        hit["from_email"] = email
        return hit
    return None


def emails_for_winner(conn, inn: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT c.value, c.party
        FROM contacts c
        JOIN lots l ON l.id = c.lot_id
        WHERE l.winner_inn = ? AND c.type = 'email'
        """,
        (inn,),
    ).fetchall()
    found: list[str] = []
    for row in rows:
        email = str(row["value"] or "").strip().lower()
        if "@" not in email or email in found:
            continue
        party = str(row["party"] or "")
        domain = email.rsplit("@", 1)[-1]
        if party == "winner" or is_free_mail(domain):
            found.append(email)
    return found


def websites_for_winner(conn, inn: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT c.value
        FROM contacts c
        JOIN lots l ON l.id = c.lot_id
        WHERE l.winner_inn = ? AND c.type = 'website'
        """,
        (inn,),
    ).fetchall()
    found: list[str] = []
    for row in rows:
        url = normalize_website(str(row["value"] or ""))
        if not url or is_junk_website(url) or url in found:
            continue
        found.append(url)
    return found


def winner_name_for_inn(conn, inn: str) -> str:
    row = conn.execute(
        "SELECT winner_name FROM lots WHERE winner_inn = ? AND winner_name IS NOT NULL LIMIT 1",
        (inn,),
    ).fetchone()
    return str(row["winner_name"] or "") if row else ""
