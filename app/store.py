"""Выборка заказов для витрины. Фильтры только параметрами, без склейки SQL из ввода."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app import db
from app.analyze import lead_analysis, summarize_document, summary_from_json, summary_to_json
from app.htmlutil import is_ru_phone, normalize_email
from app.present import (
    MISSING,
    NO_EMAIL,
    NO_PHONE,
    STATUS_LABELS,
    contact_source_label,
    is_hot,
    money,
    parse_json_list,
    profile_labels,
    ru_date,
    source_labels,
    urgency,
)

ALLOWED_STATUS = frozenset(STATUS_LABELS)
_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


def _row_contacts(conn: sqlite3.Connection, lot_id: int) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT value, type, party, source
        FROM contacts
        WHERE lot_id = ?
        ORDER BY CASE type
            WHEN 'phone' THEN 1
            WHEN 'email' THEN 2
            WHEN 'website' THEN 3
            ELSE 4
        END,
        CASE source
            WHEN 'eis_participants' THEN 1
            WHEN 'eis_card' THEN 2
            WHEN 'eis' THEN 3
            WHEN 'checko' THEN 4
            WHEN 'email_domain' THEN 5
            WHEN 'document_ocr' THEN 6
            ELSE 9
        END,
        party
        """,
        (lot_id,),
    ).fetchall()
    return [
        {
            "value": str(item["value"]),
            "type": str(item["type"]),
            "party": str(item["party"]),
            "source": str(item["source"] or ""),
            "source_label": contact_source_label(str(item["source"] or "")),
        }
        for item in rows
    ]


def _phone_map(conn: sqlite3.Connection) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for row in conn.execute(
        "SELECT lot_id, type, value FROM contacts WHERE type IN ('phone', 'email')"
    ):
        out.setdefault(int(row["lot_id"]), []).append(f"{row['type']}:{row['value']}")
    return out


def serialize_lot(
    row: sqlite3.Row,
    *,
    contacts: list[dict[str, str]] | None = None,
    phones: list[str] | None = None,
) -> dict[str, Any]:
    profiles = parse_json_list(row["profiles"])
    keywords = parse_json_list(row["matched_keywords"])
    contact_rows = contacts or []
    phone_vals = [
        item["value"]
        for item in contact_rows
        if item["type"] == "phone" and is_ru_phone(item["value"])
    ] or [
        item.split(":", 1)[1]
        for item in (phones or [])
        if item.startswith("phone:") and is_ru_phone(item.split(":", 1)[1])
    ]
    email_vals = [
        item["value"] for item in contact_rows if item["type"] == "email" and normalize_email(item["value"])
    ] or [
        item.split(":", 1)[1]
        for item in (phones or [])
        if item.startswith("email:") and normalize_email(item.split(":", 1)[1])
    ]
    score = urgency(
        amount=row["amount_rub"],
        signed_at=row["signed_at"],
        published_at=row["published_at"],
        has_phone=bool(phone_vals),
        has_email=bool(email_vals),
        has_winner=bool(row["winner_inn"] or row["winner_name"]),
        profiles=profiles,
    )
    status = row["status"] if row["status"] in ALLOWED_STATUS else "new"
    return {
        "id": int(row["id"]),
        "external_id": row["external_id"],
        "url": row["url"] or "",
        "subject": row["subject"] or MISSING,
        "amount": row["amount_rub"],
        "amount_text": money(row["amount_rub"]),
        "date": ru_date(row["signed_at"] or row["published_at"]),
        "signed_at": row["signed_at"],
        "published_at": row["published_at"],
        "fz": row["fz"] or MISSING,
        "source": row["source"] or "",
        "source_labels": source_labels(row["source"]),
        "profiles": profiles,
        "profile_labels": profile_labels(row["profiles"]),
        "keywords": keywords,
        "customer_name": row["customer_name"] or MISSING,
        "customer_inn": row["customer_inn"] or MISSING,
        "winner_name": row["winner_name"] or MISSING,
        "winner_inn": row["winner_inn"] or MISSING,
        "reason": row["reason"] or "Ключи совпали с предметом заказа.",
        "status": status,
        "status_label": STATUS_LABELS[status],
        "score": score,
        "hot": is_hot(score),
        "has_phone": bool(phone_vals),
        "phone": phone_vals[0] if phone_vals else NO_PHONE,
        "email": email_vals[0] if email_vals else NO_EMAIL,
        "contacts": [
            {
                **item,
                "source_label": item.get("source_label")
                or contact_source_label(item.get("source")),
            }
            for item in contact_rows
            if (item["type"] != "phone" or is_ru_phone(item["value"]))
            and (item["type"] != "email" or normalize_email(item["value"]))
            and not (
                str(item.get("source") or "") == "document_ocr"
                and item.get("party") != "winner"
            )
        ],
        "region": row["region_text"] or "регион не указан",
        "region_code": row["region_code"] or "",
    }


def list_lots(conn: sqlite3.Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
    phones = _phone_map(conn)
    org_names = _org_name_map(conn)
    rows = conn.execute(
        """
        SELECT * FROM lots
        ORDER BY COALESCE(signed_at, published_at, '') DESC, id DESC
        """
    ).fetchall()
    amount_min = _maybe_int(params.get("amount_min"))
    amount_max = _maybe_int(params.get("amount_max"))
    source = (params.get("source") or "").strip()
    fz = (params.get("fz") or "").strip()
    profile = (params.get("profile") or "").strip()
    status = (params.get("status") or "").strip()
    date_from = (params.get("date_from") or "").strip()
    date_to = (params.get("date_to") or "").strip()
    region = (params.get("region") or "").strip()
    q_tokens = _words(str(params.get("q") or ""))
    keyword = (params.get("keyword") or "").strip().lower()
    has_phone = (params.get("has_phone") or "").strip()
    hot_only = (params.get("hot") or "").strip() in {"1", "true", "yes"}
    items: list[dict[str, Any]] = []
    for row in rows:
        if amount_min is not None and (row["amount_rub"] is None or row["amount_rub"] < amount_min):
            continue
        if amount_max is not None and (row["amount_rub"] is None or row["amount_rub"] > amount_max):
            continue
        if source and source not in (row["source"] or ""):
            continue
        if fz and str(row["fz"] or "") != fz:
            continue
        profiles = parse_json_list(row["profiles"])
        if profile and profile not in profiles:
            continue
        if status and status in ALLOWED_STATUS and row["status"] != status:
            continue
        stamp = str(row["signed_at"] or row["published_at"] or "")[:10]
        if stamp:
            if date_from and stamp < date_from:
                continue
            if date_to and stamp > date_to:
                continue
        if region and str(row["region_code"] or "") != region and region not in str(row["region_text"] or ""):
            continue
        if q_tokens:
            lot_phones = phones.get(int(row["id"]), [])
            words, digits = _lot_haystack(row, org_names, lot_phones)
            if not _query_hits(q_tokens, words, digits):
                continue
        keys = parse_json_list(row["matched_keywords"])
        if keyword and not _keyword_hit(keyword, keys):
            continue
        item = serialize_lot(row, phones=phones.get(int(row["id"]), []))
        if has_phone == "1" and not item["has_phone"]:
            continue
        if has_phone == "0" and item["has_phone"]:
            continue
        if hot_only and not item["hot"]:
            continue
        items.append(item)
    return items


def _fold(text: str) -> str:
    return (text or "").replace("Ё", "е").replace("ё", "е").lower()


def _words(text: str) -> list[str]:
    return [part for part in _SPLIT.split(_fold(text)) if part]


def _digits(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch.isdigit())


def _org_name_map(conn: sqlite3.Connection) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    for row in conn.execute("SELECT inn, name FROM org_cache WHERE IFNULL(name, '') != ''"):
        out.setdefault(str(row["inn"]), []).append(str(row["name"]))
    return {inn: " ".join(names) for inn, names in out.items()}


def _lot_haystack(
    row: sqlite3.Row,
    org_names: dict[str, str],
    phone_items: list[str],
) -> tuple[list[str], str]:
    inn_c = str(row["customer_inn"] or "")
    inn_w = str(row["winner_inn"] or "")
    contacts = [item.split(":", 1)[-1] for item in phone_items]
    blob = " ".join(
        [
            str(row["id"]),
            str(row["external_id"] or ""),
            str(row["subject"] or ""),
            str(row["customer_name"] or ""),
            inn_c,
            str(row["winner_name"] or ""),
            inn_w,
            str(row["region_text"] or ""),
            org_names.get(inn_c, ""),
            org_names.get(inn_w, ""),
            *contacts,
        ]
    )
    return _words(blob), _digits(blob)


def _query_hits(tokens: list[str], words: list[str], digits: str) -> bool:
    glued = "".join(token for token in tokens if token.isdigit())
    if tokens and all(token.isdigit() for token in tokens) and len(glued) >= 4:
        return glued in digits
    for token in tokens:
        if token.isdigit():
            if len(token) >= 4:
                if token not in digits:
                    return False
                continue
            if token not in words:
                return False
            continue
        if len(token) < 2:
            if token not in words:
                return False
            continue
        if not any(word == token or word.startswith(token) for word in words):
            return False
    return True


def _keyword_hit(selected: str, keys: list[str]) -> bool:
    needle = selected.lower()
    if not needle:
        return True
    for item in keys:
        hay = str(item).lower()
        if needle == hay or needle in hay or hay in needle:
            return True
    return False


def _maybe_int(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    return int(raw)


def tiles(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "found": len(items),
        "profile": sum(1 for item in items if item["keywords"]),
        "winner": sum(
            1 for item in items if item["winner_inn"] != MISSING or item["winner_name"] != MISSING
        ),
        "no_contact": sum(1 for item in items if not item["has_phone"]),
        "hot": sum(1 for item in items if item["hot"]),
    }


def get_lot(conn: sqlite3.Connection, lot_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    if row is None:
        return None
    item = serialize_lot(row, contacts=_row_contacts(conn, lot_id))
    docs = conn.execute(
        """
        SELECT id, url, filename, ocr_status, ocr_text, ocr_summary
        FROM documents WHERE lot_id = ? LIMIT 40
        """,
        (lot_id,),
    ).fetchall()
    rendered: list[dict[str, Any]] = []
    analysis_docs: list[dict[str, Any]] = []
    wrote = False
    for doc in docs:
        summary = summary_from_json(doc["ocr_summary"])
        text = str(doc["ocr_text"] or "")
        stale = (
            not summary
            or summary.get("rank") is None
            or summary.get("value") not in {"read", "skip"}
            or summary.get("via") == "llm"
        )
        if text and stale:
            summary = summarize_document(text, str(doc["filename"] or ""))
            conn.execute(
                "UPDATE documents SET ocr_summary = ? WHERE id = ?",
                (summary_to_json(summary), doc["id"]),
            )
            wrote = True
        elif not (summary or {}).get("text"):
            summary = summarize_document(
                text,
                str(doc["filename"] or ""),
                status=str(doc["ocr_status"] or ""),
            )
            conn.execute(
                "UPDATE documents SET ocr_summary = ? WHERE id = ?",
                (summary_to_json(summary), doc["id"]),
            )
            wrote = True
        kind = (summary or {}).get("kind") or ""
        title = (summary or {}).get("title") or (doc["filename"] or "файл")
        body = (summary or {}).get("text") or ""
        value = (summary or {}).get("value") or "skip"
        rank_raw = (summary or {}).get("rank")
        rank = int(rank_raw) if rank_raw is not None else (0 if value == "read" else 50)
        rendered.append(
            {
                "url": doc["url"],
                "filename": title,
                "ocr_status": doc["ocr_status"] or "pending",
                "kind": kind,
                "summary": body,
                "value": value,
                "rank": rank,
            }
        )
        analysis_docs.append(
            {
                "ocr_text": text,
                "kind": kind,
                "summary": body,
                "value": value,
                "ocr_status": doc["ocr_status"] or "pending",
                "filename": str(doc["filename"] or ""),
            }
        )
    rendered.sort(
        key=lambda item: (
            item["rank"] if item.get("rank") is not None else 50,
            str(item.get("filename") or ""),
        )
    )
    if wrote:
        conn.commit()
    item["documents"] = rendered
    cached = summary_from_json(row["lead_analysis"] if "lead_analysis" in row.keys() else None)
    analysis = lead_analysis(
        subject=str(row["subject"] or ""),
        amount_text=item["amount_text"],
        profiles=item["profiles"],
        documents=analysis_docs,
    )
    item["analysis"] = analysis
    if cached != analysis:
        db.set_lot_analysis(conn, lot_id, summary_to_json(analysis))
        conn.commit()
    return item
