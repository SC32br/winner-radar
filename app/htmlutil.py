"""Разбор HTML ЕИС без тяжёлого парсера: теги, сумма, даты."""

from __future__ import annotations

import html
import re
from datetime import datetime
from urllib.parse import urlparse

_SCRIPT = re.compile(r"<script[\s\S]*?</script>", re.I)
_STYLE = re.compile(r"<style[\s\S]*?</style>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_PHONE = re.compile(
    r"(?:\+7|8|7)[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}"
)
_BARE_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_FREE_MAIL = frozenset(
    {
        "mail.ru",
        "inbox.ru",
        "bk.ru",
        "list.ru",
        "internet.ru",
        "xmail.ru",
        "yandex.ru",
        "yandex.com",
        "ya.ru",
        "gmail.com",
        "googlemail.com",
        "rambler.ru",
        "lenta.ru",
        "autorambler.ru",
        "myrambler.ru",
        "icloud.com",
        "me.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "proton.me",
        "protonmail.com",
    }
)
_SKIP_HOST_PARTS = (
    "zakupki.gov",
    "filestore",
    "gosuslugi",
    "nalog.gov",
    "roskazna",
    "sberbank",
    "sberbank-ast",
    "vk.com",
    "t.me",
    "youtube.com",
    "google.com",
    "yandex.ru",
    "dadata.ru",
    "clearspending.ru",
    "roseltorg",
    "rts-tender",
    "fabrikant",
    "tektorg",
    "etpgpb",
    "astgoz",
    "etprf",
    "zakazrf",
    "lot-online",
    "etp-ets",
    "b2b-center",
    "otc.ru",
    "kremlin.ru",
    "government.ru",
    "economy.gov.ru",
    "minfin.gov.ru",
    "fas.gov.ru",
    "w3.org",
    "w3c.org",
    "consultant.ru",
    "garant.ru",
)
_LABELED_SITE = re.compile(
    r"(?:сайт(?:\s+организации)?|веб-?сайт|адрес сайта)\s*[:\-–]\s*(\S+)",
    re.I,
)


def strip_tags(raw: str) -> str:
    text = _SCRIPT.sub(" ", raw)
    text = _STYLE.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return _SPACE.sub(" ", text).strip()


def parse_amount_rub(raw: str | None) -> int | None:
    if not raw:
        return None
    text = html.unescape(raw).replace("\xa0", " ").replace("&nbsp;", " ")
    match = re.search(r"(\d[\d\s]{1,})\s*[,.]\s*(\d{2})", text)
    if match:
        whole = re.sub(r"\D", "", match.group(1))
        if whole:
            return int(whole)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 6:
        return int(digits)
    return None


def parse_ru_date(raw: str | None) -> str | None:
    if not raw:
        return None
    match = _DATE.search(raw)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def normalize_email(raw: str | None) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    match = _EMAIL.fullmatch(text) or _EMAIL.search(text)
    if not match:
        return None
    return match.group(0).lower()


def emails(text: str) -> list[str]:
    found: list[str] = []
    for item in _EMAIL.findall(text or ""):
        mail = normalize_email(item)
        if mail and mail not in found:
            found.append(mail)
    return found


# Коды ABC/DEF российских телефонов. 1xx/2xx/0xx и куски ИНН/ИКЗ сюда не входят.
_GEO_ABC = frozenset(
    {
        "301", "302", "341", "342", "343", "345", "346", "347", "349",
        "351", "352", "353", "365", "381", "382", "383", "384", "385", "388",
        "390", "391", "394", "395",
        "401", "411", "413", "415", "416", "421", "423", "424", "426", "427",
        "471", "472", "473", "474", "475", "481", "482", "483", "484", "485",
        "486", "487", "491", "492", "493", "494", "495", "496", "498", "499",
        "800", "804", "808",
        "811", "812", "813", "814", "815", "816", "817", "818", "820", "821",
        "831", "833", "834", "835", "836",
        "841", "842", "843", "844", "845", "846", "847", "848", "851", "855",
        "861", "862", "863", "865", "866", "867", "869",
        "871", "872", "873", "877", "878", "879",
    }
)
_REPEAT = re.compile(r"(.)\1{4,}")


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        norm = "+7" + digits[1:]
    elif len(digits) == 10:
        norm = "+7" + digits
    else:
        return None
    return norm if is_ru_phone(norm) else None


def is_ru_phone(norm: str) -> bool:
    """Отсекает OCR-мусор: +71000000000, куски ИНН, нули, несуществующие коды."""
    if not norm.startswith("+7") or len(norm) != 12 or not norm[2:].isdigit():
        return False
    national = norm[2:]
    if national.count("0") >= 6:
        return False
    if _REPEAT.search(national):
        return False
    if national[:2] in {"90"} and national[2:] == "00000000":
        return False
    first = national[0]
    if first == "9":
        return True
    if first not in "348":
        return False
    if national.startswith("40102"):
        return False
    return national[:3] in _GEO_ABC


def phones(text: str) -> list[str]:
    found: list[str] = []
    for item in _PHONE.findall(text or ""):
        norm = normalize_phone(item)
        if norm and norm not in found:
            found.append(norm)
    return found


_PHONE_LOOSE = re.compile(
    r"(?:\+7|8|7)[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{2,3}[\s\-]*\d{2}[\s\-]*\d{2,3}"
)


def phones_loose(text: str) -> list[str]:
    """Сайты часто пишут +7 (812) 42-42-152, не 812-424-21-52."""
    blob = text or ""
    found: list[str] = []
    for rx in (_PHONE, _PHONE_LOOSE):
        for match in rx.finditer(blob):
            start, end = match.span()
            left = start
            while left > 0 and blob[left - 1].isdigit():
                left -= 1
            right = end
            while right < len(blob) and blob[right].isdigit():
                right += 1
            run = re.sub(r"\D", "", blob[left:right])
            if len(run) > 11:
                continue
            norm = normalize_phone(match.group())
            if norm and norm not in found:
                found.append(norm)
    return found


def abs_url(base: str, href: str) -> str:
    href = html.unescape(href).strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if not href.startswith("/"):
        href = "/" + href
    return base.rstrip("/") + href


def is_junk_host(host: str) -> bool:
    """Площадки, госпорталы и служебные домены со страницы ЕИС — не сайт фирмы."""
    low = (host or "").lower().removeprefix("www.")
    if not low or "." not in low:
        return True
    return any(part in low for part in _SKIP_HOST_PARTS)


def _host_ok(host: str) -> bool:
    return not is_junk_host(host)


def is_junk_website(url: str) -> bool:
    try:
        host = (urlparse(url or "").hostname or "").lower().removeprefix("www.")
    except ValueError:
        return True
    return is_junk_host(host)


def is_free_mail(domain: str) -> bool:
    low = (domain or "").lower().removeprefix("www.")
    return any(low == item or low.endswith("." + item) for item in _FREE_MAIL)


def normalize_website(raw: str) -> str | None:
    text = html.unescape(raw or "").strip().rstrip(".,);]")
    if not text or text in {"—", "-", "–", "нет", "не указан"}:
        return None
    if text.startswith("www."):
        text = "https://" + text
    if not text.startswith(("http://", "https://")):
        if re.fullmatch(r"(?:[A-Za-z0-9а-яА-ЯёЁ-]+\.)+[A-Za-zа-яА-ЯёЁ]{2,}", text):
            text = "https://" + text
        else:
            return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not _host_ok(host):
        return None
    return f"https://{host}"


def websites(html_raw: str, text: str = "") -> list[str]:
    """Сайты только из видимого текста, не из всех href подвала ЕИС."""
    found: list[str] = []
    blob = text or strip_tags(html_raw or "")
    for match in _LABELED_SITE.findall(blob) + _BARE_URL.findall(blob):
        site = normalize_website(match)
        if site and site not in found:
            found.append(site)
    return found


def labeled_websites(text: str) -> list[str]:
    found: list[str] = []
    for match in _LABELED_SITE.findall(text or ""):
        site = normalize_website(match)
        if site and site not in found:
            found.append(site)
    return found


def site_from_email(email: str) -> str | None:
    value = (email or "").strip().lower()
    if "@" not in value:
        return None
    domain = value.rsplit("@", 1)[-1].strip().strip(".")
    if not domain or is_free_mail(domain):
        return None
    return normalize_website("https://" + domain)


_GENERIC_LOCAL = frozenset(
    {
        "info", "mail", "office", "kontakt", "contact", "contacts", "hello",
        "admin", "webmaster", "postmaster", "sales", "sale", "tender", "tenders",
        "zakupki", "zakupka", "smp", "secretary", "sekretar", "sekretariat",
        "bux", "buh", "buhg", "hr", "kadry", "press", "docs", "document",
        "dogovor", "urist", "legal", "reception", "priem", "kanc", "inbox",
        "support", "help", "service", "smeta", "pto", "director", "direktor",
        "manager", "snab", "zakaz", "order", "orders", "torgi", "etp", "eis",
        "noreply", "no-reply", "robot", "notify", "user", "test", "post",
    }
)
_SITE_TLDS = ("ru", "рф", "com")


def _slug_from_local(local: str) -> str | None:
    raw = (local or "").strip().lower().replace("_", "-").replace(".", "-")
    raw = re.sub(r"^(ooo|ip|ao|pao|zao|nko|ano)-", "", raw)
    slug = re.sub(r"[^a-zа-яё0-9-]+", "", raw, flags=re.I).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if len(slug) < 4 or len(slug) > 40:
        return None
    letters = sum(ch.isalpha() for ch in slug)
    digits = sum(ch.isdigit() for ch in slug)
    if letters < 4 or digits > letters:
        return None
    core = re.sub(r"[^a-zа-яё]", "", slug)
    if core in _GENERIC_LOCAL:
        return None
    return slug


def email_site_candidates(email: str) -> list[str]:
    """Корп. домен после @, либо ящик до @ плюс .ru/.рф/.com."""
    value = (email or "").strip().lower()
    if "@" not in value:
        return []
    local, _, domain = value.partition("@")
    found: list[str] = []
    corp = site_from_email(value)
    if corp:
        found.append(corp)
    elif is_free_mail(domain):
        slug = _slug_from_local(local)
        if slug:
            for tld in _SITE_TLDS:
                site = normalize_website(f"https://{slug}.{tld}")
                if site and site not in found:
                    found.append(site)
    return found
