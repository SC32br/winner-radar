"""Скачивание вложений ЕИС, текст PDF/DOCX и OCR сканов → контакты."""

from __future__ import annotations

import io
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import pymupdf

from app import config, db
from app.analyze import enrich_summary, ocr_is_garbage, summarize_document, summary_to_json
from app.collectors.eis import DOCS, parse_documents
from app.htmlutil import emails, is_free_mail, phones_loose, strip_tags
from app.http_client import FetchError, eis_http
from app.logutil import LotLog

WINNER_HINTS = ("победитель", "поставщик (подрядчик", "информация о поставщике")
CUSTOMER_HINTS = (
    "заказчик",
    "покупатель",
    "контрактный управляющий",
    "должностное лицо заказчика",
    "информация о заказчике",
)
_DISPOSITION = re.compile(r"filename\*?=(?:UTF-8''|\"?)([^;\"]+)", re.I)


def _safe_name(raw: str) -> str:
    name = unquote(raw or "file").replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^\w.\-а-яА-ЯёЁ ]+", "_", name, flags=re.I).strip("._ ") or "file"
    return name[:80]


def _filename_from_headers(headers: dict[str, str], fallback: str) -> str:
    match = _DISPOSITION.search(headers.get("content-disposition") or "")
    if match:
        return _safe_name(unquote(match.group(1)))
    return _safe_name(fallback)


def _kind(data: bytes, content_type: str, name: str) -> str:
    head = data[:8]
    low = (name or "").lower()
    ctype = (content_type or "").lower()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        names = _zip_member_names(data)
        joined = " ".join(names).lower()
        if "word/document.xml" in joined:
            return "docx"
        if "xl/sharedstrings.xml" in joined or "xl/workbook.xml" in joined:
            return "xlsx"
        if names:
            return "zip"
        if "xl/" in low or "xlsx" in low or "spreadsheet" in ctype:
            return "xlsx"
        return "docx"
    if head.startswith(b"\xff\xd8") or head.startswith(b"\x89PNG") or head[:4] in {b"II*\x00", b"MM\x00*"}:
        return "image"
    if data.lstrip()[:5] == b"<?xml" or low.endswith(".xml"):
        return "xml"
    if b"<html" in data[:400].lower() or data[:32].lstrip().startswith(b"<!DOCTYPE"):
        return "html"
    if "pdf" in ctype or low.endswith(".pdf"):
        return "pdf"
    if "word" in ctype or low.endswith(".docx"):
        return "docx"
    if low.endswith(".xlsx"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0") and (low.endswith(".doc") or "msword" in ctype):
        return "doc"
    if ctype.startswith("image/") or low.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")):
        return "image"
    return "other"


def _zip_member_names(data: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return [item.filename.replace("\\", "/") for item in archive.infolist()]
    except zipfile.BadZipFile:
        return []


def _zip_archive_text(data: bytes, *, min_chars: int, max_pages: int, depth: int = 0) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return ""
    chunks: list[str] = []
    with archive:
        for item in archive.infolist():
            if item.is_dir() or item.file_size <= 0:
                continue
            name = item.filename.replace("\\", "/")
            base = name.rsplit("/", 1)[-1]
            if not base or base.startswith(".") or "__macosx" in name.lower():
                continue
            try:
                inner = archive.read(item)
            except (KeyError, RuntimeError):
                continue
            inner_kind = _kind(inner, "", base)
            if inner_kind == "zip" and depth < 1:
                text = _zip_archive_text(
                    inner, min_chars=min_chars, max_pages=max_pages, depth=depth + 1
                )
            elif inner_kind in {"zip", "other"}:
                continue
            else:
                text = extract_text(inner, inner_kind, min_chars=min_chars, max_pages=max_pages)
            if text.strip():
                chunks.append(f"{base}\n{text.strip()}")
    return "\n\n".join(chunks)


def _zip_text(data: bytes, inner: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read(inner)
    except (KeyError, zipfile.BadZipFile):
        return ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    parts = [node.text for node in root.iter() if node.text and node.text.strip()]
    return " ".join(parts)


def _ocr_page(page, dpi: int = 140) -> str:
    pix = page.get_pixmap(dpi=dpi)
    png = pix.tobytes("png")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(png)
        path = tmp.name
    try:
        proc = subprocess.run(
            ["tesseract", path, "stdout", "-l", "rus+eng"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        return proc.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""
    finally:
        Path(path).unlink(missing_ok=True)


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _xlsx_table_text(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return ""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    strings: list[str] = []
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        root = None
    if root is not None:
        for node in root.iter(f"{ns}si"):
            bits = [item.text or "" for item in node.iter(f"{ns}t")]
            strings.append("".join(bits))
    sheets = sorted(
        name
        for name in archive.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )
    lines: list[str] = []
    for sheet in sheets[:4]:
        try:
            body = ET.fromstring(archive.read(sheet))
        except ET.ParseError:
            continue
        for row in body.iter(f"{ns}row"):
            cells: dict[int, str] = {}
            for cell in row.findall(f"{ns}c"):
                ref = cell.get("r") or ""
                letters = "".join(ch for ch in ref if ch.isalpha())
                idx = 0
                for ch in letters:
                    idx = idx * 26 + (ord(ch.upper()) - 64)
                idx = max(0, idx - 1)
                raw = cell.findtext(f"{ns}v") or ""
                kind = cell.get("t")
                if kind == "s" and raw.isdigit() and int(raw) < len(strings):
                    value = strings[int(raw)]
                elif kind == "inlineStr":
                    value = "".join(item.text or "" for item in cell.iter(f"{ns}t"))
                else:
                    value = _excel_number(raw) if raw else ""
                if value:
                    cells[idx] = value
            if not cells:
                continue
            line = " ".join(cells[i] for i in sorted(cells))
            if line.strip():
                lines.append(line.strip())
    return "\n".join(lines)


def _excel_number(raw: str) -> str:
    try:
        number = float(raw)
    except ValueError:
        return raw
    if abs(number - round(number)) < 1e-9 and abs(number) < 1_000_000_000:
        if abs(number) >= 100:
            return f"{int(round(number)):,}".replace(",", " ")
        return str(int(round(number)))
    if abs(number) >= 100:
        text = f"{number:,.2f}"
        return text.replace(",", "X").replace(".", ",").replace("X", " ")
    return raw


def _ole_doc_text(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        for cmd in (["antiword", path], ["catdoc", "-d", "cp1251", path]):
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=40, check=False)
            except (OSError, subprocess.TimeoutExpired):
                continue
            raw = proc.stdout or b""
            if not raw.strip():
                continue
            for enc in ("utf-8", "cp1251"):
                try:
                    text = raw.decode(enc)
                except UnicodeDecodeError:
                    continue
                if text.strip():
                    return text
            return raw.decode("utf-8", errors="replace")
        return ""
    finally:
        Path(path).unlink(missing_ok=True)


def extract_text(data: bytes, kind: str, *, min_chars: int, max_pages: int) -> str:
    if kind in {"html", "xml"}:
        return strip_tags(_decode_text(data))
    if kind == "zip":
        return _zip_archive_text(data, min_chars=min_chars, max_pages=max_pages)
    if kind == "docx":
        return _zip_text(data, "word/document.xml")
    if kind == "xlsx":
        table = _xlsx_table_text(data)
        return table if table.strip() else _zip_text(data, "xl/sharedStrings.xml")
    if kind == "doc":
        return _ole_doc_text(data)
    if kind in {"pdf", "image"}:
        filetype = "pdf"
        if kind == "image":
            if data.startswith(b"\xff\xd8"):
                filetype = "jpeg"
            elif data.startswith(b"\x89PNG"):
                filetype = "png"
            else:
                filetype = None
        try:
            doc = pymupdf.open(stream=data, filetype=filetype)
        except Exception:
            return ""
        try:
            chunks: list[str] = []
            for index, page in enumerate(doc):
                if index >= max_pages:
                    break
                layer = (page.get_text() or "").strip()
                if len(layer) < min_chars:
                    layer = (_ocr_page(page) or layer).strip()
                if layer:
                    chunks.append(layer)
            return "\n".join(chunks)
        finally:
            doc.close()
    return ""


def _inn_before(text: str, pos: int, inn: str, max_dist: int = 900) -> int | None:
    clean = re.sub(r"\D", "", inn or "")
    if len(clean) < 10 or pos < 0:
        return None
    best: int | None = None
    start = 0
    while True:
        idx = text.find(clean, start)
        if idx < 0 or idx > pos:
            break
        dist = pos - idx
        if dist <= max_dist and (best is None or dist < best):
            best = dist
        start = idx + 1
    return best


def _value_pos(text: str, value: str, typ: str) -> int:
    if typ == "email":
        return text.lower().find(value.lower())
    tail = re.sub(r"\D", "", value or "")[-10:]
    return text.find(tail) if len(tail) == 10 else -1


def _guess_party(
    text: str,
    pos: int,
    winner_name: str,
    customer_name: str,
    winner_inn: str = "",
    customer_inn: str = "",
) -> str:
    win_d = _inn_before(text, pos, winner_inn)
    cus_d = _inn_before(text, pos, customer_inn)
    if win_d is not None and (cus_d is None or win_d <= cus_d):
        return "winner"
    snippet = text[max(0, pos - 220) : pos + 220] if pos >= 0 else text[:440]
    low = snippet.lower().replace("ё", "е")
    win = (winner_name or "").lower().replace("ё", "е")
    cus = (customer_name or "").lower().replace("ё", "е")
    win_bits = [part for part in re.findall(r"[а-яa-z]{4,}", win) if part not in {"общество", "ограниченной", "ответственностью"}]
    cus_bits = [part for part in re.findall(r"[а-яa-z]{4,}", cus) if part not in {"общество", "ограниченной", "ответственностью", "казенное", "учреждение"}]
    if any(part in low for part in win_bits[:4]):
        return "winner"
    if any(part in low for part in cus_bits[:4]):
        return "customer"
    if any(hint in low for hint in WINNER_HINTS):
        return "winner"
    if any(hint in low for hint in CUSTOMER_HINTS):
        return "customer"
    if cus_d is not None:
        return "customer"
    return "unknown"


def _snippet(text: str, needle: str) -> str:
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return text[:160]
    start = max(0, idx - 80)
    return text[start : idx + len(needle) + 80]


def _keep_doc_contact(item: dict[str, str], winner_name: str) -> bool:
    if item["type"] == "website":
        return False
    if item["party"] != "winner":
        return False
    if item["type"] != "email":
        return True
    mail = item["value"]
    domain = mail.rsplit("@", 1)[-1]
    folded = (winner_name or "").lower().replace("ё", "е")
    slug = domain.split(".")[0]
    if len(slug) >= 4 and slug in folded.replace(" ", "").replace("-", ""):
        return True
    local = mail.rsplit("@", 1)[0]
    if any(bit in local for bit in re.findall(r"[а-яa-z]{4,}", folded)[:4]):
        return True
    return is_free_mail(domain)


def contacts_from_text(
    text: str,
    winner_name: str,
    customer_name: str,
    winner_inn: str = "",
    customer_inn: str = "",
) -> list[dict[str, str]]:
    if ocr_is_garbage(text):
        return []
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for phone in phones_loose(text):
        key = ("phone", phone)
        if key in seen:
            continue
        seen.add(key)
        pos = _value_pos(text, phone, "phone")
        snippet = _snippet(text, phone[-10:])
        item = {
            "value": phone,
            "type": "phone",
            "party": _guess_party(
                text, pos, winner_name, customer_name, winner_inn, customer_inn
            ),
            "snippet": snippet[:500],
        }
        if _keep_doc_contact(item, winner_name):
            found.append(item)
    for mail in emails(text):
        key = ("email", mail)
        if key in seen:
            continue
        seen.add(key)
        pos = _value_pos(text, mail, "email")
        snippet = _snippet(text, mail)
        item = {
            "value": mail,
            "type": "email",
            "party": _guess_party(
                text, pos, winner_name, customer_name, winner_inn, customer_inn
            ),
            "snippet": snippet[:500],
        }
        if _keep_doc_contact(item, winner_name):
            found.append(item)
    return found


def _contacts_from_row(row, text: str) -> list[dict[str, str]]:
    keys = set(row.keys())
    return contacts_from_text(
        text,
        str(row["winner_name"] or ""),
        str(row["customer_name"] or ""),
        str(row["winner_inn"] or "") if "winner_inn" in keys else "",
        str(row["customer_inn"] or "") if "customer_inn" in keys else "",
    )


def _store_contacts(conn, lot_id: int, rows: list[dict[str, str]]) -> int:
    added = 0
    for item in rows:
        if db.add_contact_if_new(
            conn,
            lot_id,
            value=item["value"],
            type=item["type"],
            party=item["party"],
            source="document_ocr",
            confidence=0.7 if item["party"] != "unknown" else 0.55,
            snippet=item.get("snippet"),
        ):
            added += 1
    return added


def fetch_missing_lists(
    conn, log: LotLog, collect_cfg: dict, lot_id: int | None = None
) -> int:
    limit = int(collect_cfg.get("max_doc_lists") or 8)
    sleep_sec = float(collect_cfg.get("sleep_sec") or 0.35)
    rows = db.lots_without_documents(conn, limit, lot_id=lot_id)
    if not rows:
        return 0
    client = eis_http(sleep_sec, timeout=90.0)
    added = 0
    try:
        for row in rows:
            item_log = LotLog(log.logger, {"external_id": row["external_id"]})
            try:
                html = client.get_text(DOCS, params={"reestrNumber": row["external_id"]})
            except FetchError as exc:
                item_log.info("список доков ошибка %s", exc)
                continue
            files = parse_documents(html)
            for doc in files:
                if db.add_document_if_new(conn, int(row["id"]), url=doc.url, filename=doc.filename):
                    added += 1
            item_log.info("список доков files=%s", len(files))
            conn.commit()
    finally:
        client.close()
    return added


def process_pending(
    conn, log: LotLog, collect_cfg: dict, lot_id: int | None = None
) -> dict[str, int]:
    max_docs = int(collect_cfg.get("max_docs") or 30)
    max_mb = int(collect_cfg.get("max_file_mb") or 30)
    min_chars = int(collect_cfg.get("min_ocr_chars") or 80)
    max_pages = int(collect_cfg.get("max_ocr_pages") or 8)
    sleep_sec = float(collect_cfg.get("sleep_sec") or 0.35)
    max_bytes = max_mb * 1024 * 1024
    stats = {"done": 0, "contacts": 0, "errors": 0, "skipped": 0}
    pending = db.pending_documents(conn, max_docs, lot_id=lot_id)
    if not pending:
        return stats
    client = eis_http(max(0.4, sleep_sec), timeout=90.0)
    root = Path(config.DATA_DIR) / "docs"
    try:
        for row in pending:
            item_log = LotLog(log.logger, {"external_id": row["external_id"]})
            folder = root / str(row["external_id"])
            folder.mkdir(parents=True, exist_ok=True)
            try:
                data, headers = client.get_bytes(str(row["url"]), max_bytes=max_bytes)
            except FetchError as exc:
                msg = str(exc)
                status = "skipped_size" if "слишком большой" in msg else "error"
                db.update_document(conn, int(row["id"]), ocr_status=status)
                stats["errors" if status == "error" else "skipped"] += 1
                item_log.info("док %s", status)
                conn.commit()
                continue
            name = _filename_from_headers(headers, str(row["filename"] or "file"))
            kind = _kind(data, headers.get("content-type") or "", name)
            path = folder / f"{row['id']}_{name}"
            path.write_bytes(data)
            text = extract_text(data, kind, min_chars=min_chars, max_pages=max_pages)
            status = "done" if text.strip() else ("skipped_type" if kind == "other" else "empty")
            if kind == "other":
                db.update_document(
                    conn,
                    int(row["id"]),
                    local_path=str(path),
                    mime=kind,
                    ocr_status="skipped_type",
                    ocr_summary=summary_to_json(
                        summarize_document("", name, status="skipped_type")
                    ),
                )
                stats["skipped"] += 1
                item_log.info("док пропуск тип=%s", kind)
                conn.commit()
                continue
            if text.strip() and kind in {"html", "xml", "zip"}:
                summary = summarize_document(text, name)
            elif text.strip():
                summary = enrich_summary(text, name)
            else:
                summary = summarize_document("", name, status=status)
            db.update_document(
                conn,
                int(row["id"]),
                local_path=str(path),
                mime=kind,
                ocr_status=status,
                ocr_text=text[:200000] if text else None,
                ocr_summary=summary_to_json(summary) if summary else None,
            )
            added = 0
            if text:
                added = _store_contacts(
                    conn,
                    int(row["lot_id"]),
                    _contacts_from_row(row, text),
                )
                stats["contacts"] += added
            stats["done"] += 1
            item_log.info("док %s kind=%s chars=%s contacts=%s", status, kind, len(text), added)
            conn.commit()
    finally:
        client.close()
    return stats


def reparse_markup_docs(
    conn, log: LotLog, collect_cfg: dict, lot_id: int | None = None
) -> dict[str, int]:
    min_chars = int(collect_cfg.get("min_ocr_chars") or 80)
    max_pages = int(collect_cfg.get("max_ocr_pages") or 8)
    stats = {"done": 0, "contacts": 0}
    if lot_id is not None:
        rows = conn.execute(
            """
            SELECT d.id, d.lot_id, d.local_path, d.filename, d.mime,
                   l.external_id, l.winner_name, l.customer_name, l.winner_inn, l.customer_inn
            FROM documents d
            JOIN lots l ON l.id = d.lot_id
            WHERE d.lot_id = ?
              AND d.ocr_status = 'skipped_type'
              AND d.local_path IS NOT NULL
            """,
            (lot_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT d.id, d.lot_id, d.local_path, d.filename, d.mime,
                   l.external_id, l.winner_name, l.customer_name, l.winner_inn, l.customer_inn
            FROM documents d
            JOIN lots l ON l.id = d.lot_id
            WHERE d.ocr_status = 'skipped_type'
              AND d.local_path IS NOT NULL
            ORDER BY l.amount_rub DESC
            LIMIT 40
            """
        ).fetchall()
    for row in rows:
        path = Path(str(row["local_path"]))
        if not path.is_file():
            continue
        data = path.read_bytes()
        kind = _kind(data, "", str(row["filename"] or path.name))
        if kind not in {"html", "xml"}:
            continue
        item_log = LotLog(log.logger, {"external_id": row["external_id"]})
        text = extract_text(data, kind, min_chars=min_chars, max_pages=max_pages)
        status = "done" if text.strip() else "empty"
        summary = summarize_document(text, str(row["filename"] or "")) if text.strip() else None
        db.update_document(
            conn,
            int(row["id"]),
            local_path=str(path),
            mime=kind,
            ocr_status=status,
            ocr_text=text[:200000] if text else None,
            ocr_summary=summary_to_json(summary) if summary else None,
        )
        added = 0
        if text:
            added = _store_contacts(
                conn,
                int(row["lot_id"]),
                _contacts_from_row(row, text),
            )
            stats["contacts"] += added
        stats["done"] += 1
        item_log.info("док html/xml %s kind=%s chars=%s contacts=%s", status, kind, len(text), added)
        conn.commit()
    return stats


def reparse_empty_archives(
    conn,
    log: LotLog,
    collect_cfg: dict,
    lot_id: int | None = None,
    *,
    limit: int | None = 40,
) -> dict[str, int]:
    min_chars = int(collect_cfg.get("min_ocr_chars") or 80)
    max_pages = int(collect_cfg.get("max_ocr_pages") or 8)
    stats = {"done": 0, "contacts": 0, "text": 0}
    sql = """
        SELECT d.id, d.lot_id, d.local_path, d.filename, d.mime,
               l.external_id, l.winner_name, l.customer_name, l.winner_inn, l.customer_inn
        FROM documents d
        JOIN lots l ON l.id = d.lot_id
        WHERE d.ocr_status = 'empty'
          AND d.local_path IS NOT NULL
    """
    params: list[Any] = []
    if lot_id is not None:
        sql += " AND d.lot_id = ?"
        params.append(lot_id)
    sql += " ORDER BY l.amount_rub DESC, d.id"
    if limit is not None and lot_id is None:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    for row in rows:
        path = Path(str(row["local_path"]))
        if not path.is_file():
            continue
        data = path.read_bytes()
        kind = _kind(data, "", str(row["filename"] or path.name))
        item_log = LotLog(log.logger, {"external_id": row["external_id"]})
        text = extract_text(data, kind, min_chars=min_chars, max_pages=max_pages)
        status = "done" if text.strip() else ("skipped_type" if kind == "other" else "empty")
        summary = summarize_document(text, str(row["filename"] or path.name), status=status)
        db.update_document(
            conn,
            int(row["id"]),
            local_path=str(path),
            mime=kind,
            ocr_status=status,
            ocr_text=text[:200000] if text.strip() else None,
            ocr_summary=summary_to_json(summary),
        )
        added = 0
        if text.strip():
            added = _store_contacts(
                conn,
                int(row["lot_id"]),
                _contacts_from_row(row, text),
            )
            stats["contacts"] += added
            stats["text"] += 1
        stats["done"] += 1
        item_log.info(
            "док архив %s kind=%s chars=%s contacts=%s",
            status,
            kind,
            len(text),
            added,
        )
        conn.commit()
    return stats


def purge_file_noise(conn, lot_id: int | None = None) -> int:
    dropped = 0
    if lot_id is not None:
        dropped += conn.execute(
            "DELETE FROM contacts WHERE source = 'document_ocr' AND type = 'website' AND lot_id = ?",
            (lot_id,),
        ).rowcount
        dropped += conn.execute(
            """
            DELETE FROM contacts
            WHERE source = 'document_ocr' AND type = 'email' AND lot_id = ?
              AND (value LIKE '%@fcc.ru' OR value LIKE '%@fec.ru' OR value LIKE '%@gov.ru')
            """,
            (lot_id,),
        ).rowcount
    else:
        dropped += conn.execute(
            "DELETE FROM contacts WHERE source = 'document_ocr' AND type = 'website'"
        ).rowcount
        dropped += conn.execute(
            """
            DELETE FROM contacts
            WHERE source = 'document_ocr' AND type = 'email'
              AND (value LIKE '%@fcc.ru' OR value LIKE '%@fec.ru' OR value LIKE '%@gov.ru')
            """
        ).rowcount
    conn.commit()
    return dropped


def enrich_documents(
    conn, log: LotLog, collect_cfg: dict, lot_id: int | None = None
) -> dict[str, int]:
    listed = fetch_missing_lists(conn, log, collect_cfg, lot_id=lot_id)
    processed = process_pending(conn, log, collect_cfg, lot_id=lot_id)
    noise = purge_file_noise(conn, lot_id=lot_id)
    markup = reparse_markup_docs(conn, log, collect_cfg, lot_id=lot_id)
    processed["done"] += markup.get("done") or 0
    processed["contacts"] += markup.get("contacts") or 0
    processed["markup"] = markup.get("done") or 0
    processed["purged"] = noise
    listed = fetch_missing_lists(conn, log, collect_cfg, lot_id=lot_id)
    processed = process_pending(conn, log, collect_cfg, lot_id=lot_id)
    markup = reparse_markup_docs(conn, log, collect_cfg, lot_id=lot_id)
    processed["done"] += markup.get("done") or 0
    processed["contacts"] += markup.get("contacts") or 0
    processed["markup"] = markup.get("done") or 0
    archives = reparse_empty_archives(conn, log, collect_cfg, lot_id=lot_id)
    processed["done"] += archives.get("done") or 0
    processed["contacts"] += archives.get("contacts") or 0
    processed["archives"] = archives.get("done") or 0
    upgraded = upgrade_summaries(conn, log, collect_cfg, lot_id=lot_id)
    leads = cache_lot_leads(conn, log, collect_cfg, lot_id=lot_id)
    processed["listed"] = listed
    processed["summaries"] = upgraded
    processed["leads"] = leads
    return processed


def upgrade_summaries(
    conn, log: LotLog, collect_cfg: dict, lot_id: int | None = None
) -> int:
    from app import llm
    from app.analyze import summary_from_json

    if not llm.enabled():
        return 0
    limit = min(12, int(collect_cfg.get("max_docs") or 12))
    if lot_id is not None:
        rows = conn.execute(
            """
            SELECT id, filename, ocr_text, ocr_summary, ocr_status
            FROM documents
            WHERE lot_id = ? AND ocr_text IS NOT NULL AND length(ocr_text) > 80
              AND IFNULL(mime, '') NOT IN ('html', 'xml')
            ORDER BY id
            LIMIT ?
            """,
            (lot_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, filename, ocr_text, ocr_summary, ocr_status
            FROM documents
            WHERE ocr_text IS NOT NULL AND length(ocr_text) > 80
              AND IFNULL(mime, '') NOT IN ('html', 'xml')
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    done = 0
    for row in rows:
        current = summary_from_json(row["ocr_summary"]) or {}
        if current.get("via") == "llm":
            continue
        if ocr_is_garbage(str(row["ocr_text"] or "")):
            continue
        summary = enrich_summary(str(row["ocr_text"]), str(row["filename"] or ""))
        db.update_document(
            conn,
            int(row["id"]),
            ocr_status=str(row["ocr_status"] or "done"),
            ocr_summary=summary_to_json(summary),
        )
        done += 1
        conn.commit()
    if done:
        log.info("llm саммари файлов=%s", done)
    return done


def cache_lot_leads(
    conn, log: LotLog, collect_cfg: dict, lot_id: int | None = None
) -> int:
    from app.analyze import lead_analysis, summary_from_json, summary_to_json
    from app.present import money, parse_json_list

    limit = min(8, int(collect_cfg.get("max_docs") or 8))
    if lot_id is not None:
        rows = conn.execute(
            """
            SELECT id, subject, amount_rub, profiles, lead_analysis
            FROM lots
            WHERE id = ?
            """,
            (lot_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT l.id, l.subject, l.amount_rub, l.profiles, l.lead_analysis
            FROM lots l
            JOIN documents d ON d.lot_id = l.id
            WHERE d.ocr_text IS NOT NULL AND length(d.ocr_text) > 40
            ORDER BY l.amount_rub DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    done = 0
    for row in rows:
        docs = conn.execute(
            """
            SELECT filename, ocr_status, ocr_text, ocr_summary
            FROM documents
            WHERE lot_id = ?
            ORDER BY length(ocr_text) DESC
            LIMIT 40
            """,
            (row["id"],),
        ).fetchall()
        analysis_docs = []
        for doc in docs:
            summary = summary_from_json(doc["ocr_summary"]) or {}
            analysis_docs.append(
                {
                    "ocr_text": doc["ocr_text"],
                    "kind": summary.get("kind"),
                    "summary": summary.get("text"),
                    "value": summary.get("value"),
                    "ocr_status": doc["ocr_status"],
                    "filename": doc["filename"],
                }
            )
        base = lead_analysis(
            subject=str(row["subject"] or ""),
            amount_text=money(row["amount_rub"]),
            profiles=parse_json_list(row["profiles"]),
            documents=analysis_docs,
        )
        final = base
        db.set_lot_analysis(conn, int(row["id"]), summary_to_json(final))
        done += 1
        conn.commit()
    if done:
        log.info("разбор лидов=%s", done)
    return done
