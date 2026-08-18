"""SQLite access. WAL so the dashboard and collect CLI can run together."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import config

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    db = connect() if own else conn
    assert db is not None
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate_org_cache(db)
    _migrate_documents(db)
    _migrate_lots(db)
    backfill_websites_from_email(db)
    purge_fake_phones(db)
    purge_junk_websites(db)
    purge_junk_emails(db)
    from app.seed import seed_if_empty
    seed_if_empty(db)
    db.commit()
    if own:
        db.close()


def _migrate_documents(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "ocr_summary" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN ocr_summary TEXT")


def _migrate_lots(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(lots)")}
    if "lead_analysis" not in cols:
        conn.execute("ALTER TABLE lots ADD COLUMN lead_analysis TEXT")


def _migrate_org_cache(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='org_cache'"
    ).fetchone()
    sql = (row["sql"] if row else "") or ""
    compact = sql.replace(" ", "").replace("\n", "")
    if "PRIMARY KEY (inn, source)" in sql or "PRIMARY KEY(inn,source)" in compact:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS org_cache_new (
            inn TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            ogrn TEXT,
            name TEXT,
            status TEXT,
            payload TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (inn, source)
        );
        INSERT OR IGNORE INTO org_cache_new (inn, source, ogrn, name, status, payload, updated_at)
        SELECT inn, COALESCE(NULLIF(source, ''), 'legacy'), ogrn, name, status, payload, updated_at
        FROM org_cache;
        DROP TABLE org_cache;
        ALTER TABLE org_cache_new RENAME TO org_cache;
        """
    )


def add_event(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    external_id: str | None = None,
    lot_id: int | None = None,
    payload: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO events (lot_id, external_id, type, payload, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (lot_id, external_id, event_type, payload, utc_now()),
    )


def event_exists(conn: sqlite3.Connection, event_type: str, external_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM events WHERE type = ? AND external_id = ? LIMIT 1",
        (event_type, external_id),
    ).fetchone()
    return row is not None


def upsert_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, utc_now()),
    )


_LOT_FIELDS = (
    "source",
    "url",
    "subject",
    "amount_rub",
    "currency",
    "region_code",
    "region_text",
    "published_at",
    "signed_at",
    "fz",
    "okpd_codes",
    "matched_keywords",
    "customer_name",
    "customer_inn",
    "winner_name",
    "winner_inn",
    "winner_status",
    "score",
    "profiles",
    "reason",
)


def _merge_source(old: str | None, new: str | None) -> str:
    parts: list[str] = []
    for chunk in ((old or "") + "," + (new or "")).split(","):
        item = chunk.strip()
        if item and item not in parts:
            parts.append(item)
    return ",".join(parts)


def upsert_lot(conn: sqlite3.Connection, row: dict) -> tuple[int, bool]:
    """Insert or fill empty fields. Manager status is never overwritten."""
    external_id = str(row["external_id"])
    now = utc_now()
    existing = conn.execute(
        "SELECT * FROM lots WHERE external_id = ?", (external_id,)
    ).fetchone()
    values = {key: row.get(key) for key in _LOT_FIELDS}
    values["currency"] = values.get("currency") or "RUB"
    if existing is None:
        conn.execute(
            """
            INSERT INTO lots (
                external_id, source, url, subject, amount_rub, currency,
                region_code, region_text, published_at, signed_at, fz, okpd_codes,
                matched_keywords, customer_name, customer_inn, winner_name,
                winner_inn, winner_status, score, profiles, reason, status,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                external_id,
                values.get("source"),
                values.get("url"),
                values.get("subject"),
                values.get("amount_rub"),
                values.get("currency"),
                values.get("region_code"),
                values.get("region_text"),
                values.get("published_at"),
                values.get("signed_at"),
                values.get("fz"),
                values.get("okpd_codes"),
                values.get("matched_keywords"),
                values.get("customer_name"),
                values.get("customer_inn"),
                values.get("winner_name"),
                values.get("winner_inn"),
                values.get("winner_status"),
                values.get("score"),
                values.get("profiles"),
                values.get("reason"),
                row.get("status") or "new",
                now,
                now,
            ),
        )
        lot_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        return lot_id, True

    merged_source = _merge_source(existing["source"], values.get("source"))
    conn.execute(
        """
        UPDATE lots SET
            source = ?,
            url = CASE WHEN url IS NULL OR url = '' THEN ? ELSE url END,
            subject = CASE WHEN subject IS NULL OR subject = '' THEN ? ELSE subject END,
            amount_rub = COALESCE(amount_rub, ?),
            currency = CASE WHEN currency IS NULL OR currency = '' THEN ? ELSE currency END,
            region_code = CASE WHEN region_code IS NULL OR region_code = '' THEN ? ELSE region_code END,
            region_text = CASE WHEN region_text IS NULL OR region_text = '' THEN ? ELSE region_text END,
            published_at = CASE WHEN published_at IS NULL OR published_at = '' THEN ? ELSE published_at END,
            signed_at = CASE WHEN signed_at IS NULL OR signed_at = '' THEN ? ELSE signed_at END,
            fz = CASE WHEN fz IS NULL OR fz = '' THEN ? ELSE fz END,
            okpd_codes = CASE WHEN okpd_codes IS NULL OR okpd_codes = '' THEN ? ELSE okpd_codes END,
            matched_keywords = CASE WHEN matched_keywords IS NULL OR matched_keywords = '' THEN ? ELSE matched_keywords END,
            customer_name = CASE WHEN customer_name IS NULL OR customer_name = '' THEN ? ELSE customer_name END,
            customer_inn = CASE WHEN customer_inn IS NULL OR customer_inn = '' THEN ? ELSE customer_inn END,
            winner_name = CASE WHEN winner_name IS NULL OR winner_name = '' THEN ? ELSE winner_name END,
            winner_inn = CASE WHEN winner_inn IS NULL OR winner_inn = '' THEN ? ELSE winner_inn END,
            winner_status = CASE WHEN winner_status IS NULL OR winner_status = '' THEN ? ELSE winner_status END,
            score = COALESCE(score, ?),
            profiles = CASE WHEN profiles IS NULL OR profiles = '' THEN ? ELSE profiles END,
            reason = CASE WHEN reason IS NULL OR reason = '' THEN ? ELSE reason END,
            updated_at = ?
        WHERE id = ?
        """,
        (
            merged_source,
            values.get("url"),
            values.get("subject"),
            values.get("amount_rub"),
            values.get("currency"),
            values.get("region_code"),
            values.get("region_text"),
            values.get("published_at"),
            values.get("signed_at"),
            values.get("fz"),
            values.get("okpd_codes"),
            values.get("matched_keywords"),
            values.get("customer_name"),
            values.get("customer_inn"),
            values.get("winner_name"),
            values.get("winner_inn"),
            values.get("winner_status"),
            values.get("score"),
            values.get("profiles"),
            values.get("reason"),
            now,
            existing["id"],
        ),
    )
    return int(existing["id"]), False


def add_contact_if_new(
    conn: sqlite3.Connection,
    lot_id: int,
    *,
    value: str,
    type: str,
    party: str,
    source: str | None = None,
    confidence: float | None = None,
    snippet: str | None = None,
) -> bool:
    found = conn.execute(
        """
        SELECT 1 FROM contacts
        WHERE lot_id = ? AND value = ? AND type = ? AND IFNULL(source, '') = ?
        LIMIT 1
        """,
        (lot_id, value, type, source or ""),
    ).fetchone()
    if found:
        return False
    conn.execute(
        """
        INSERT INTO contacts (lot_id, value, type, party, source, confidence, snippet)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (lot_id, value, type, party, source, confidence, snippet),
    )
    return True


def backfill_websites_from_email(conn: sqlite3.Connection) -> int:
    from app.htmlutil import site_from_email

    added = 0
    rows = conn.execute(
        "SELECT lot_id, party, value FROM contacts WHERE type = 'email'"
    ).fetchall()
    for row in rows:
        site = site_from_email(str(row["value"]))
        if not site:
            continue
        if add_contact_if_new(
            conn,
            int(row["lot_id"]),
            value=site,
            type="website",
            party=str(row["party"] or "winner"),
            source="email_domain",
            confidence=0.7,
            snippet=str(row["value"]),
        ):
            added += 1
    return added


def winner_inns_without_website(conn: sqlite3.Connection) -> list[str]:
    return winner_inns_missing_source(conn, "winner_site")


def winner_inns_missing_source(conn: sqlite3.Connection, source: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT winner_inn
        FROM lots
        WHERE winner_inn IS NOT NULL
          AND length(winner_inn) >= 10
          AND winner_inn NOT IN (
              SELECT l.winner_inn
              FROM lots l
              JOIN contacts c ON c.lot_id = l.id
              WHERE c.source = ? AND l.winner_inn IS NOT NULL
          )
        """,
        (source,),
    ).fetchall()
    return [str(row["winner_inn"]) for row in rows if row["winner_inn"]]


def attach_winner_website(conn: sqlite3.Connection, inn: str, url: str) -> int:
    return attach_winner_contacts(conn, inn, website=url)


def attach_winner_contacts(
    conn: sqlite3.Connection,
    inn: str,
    *,
    website: str | None = None,
    phones: list[str] | None = None,
    emails: list[str] | None = None,
    source: str = "checko",
) -> int:
    added = 0
    from app.htmlutil import normalize_email

    rows = conn.execute("SELECT id FROM lots WHERE winner_inn = ?", (inn,)).fetchall()
    for row in rows:
        lot_id = int(row["id"])
        if website and add_contact_if_new(
            conn,
            lot_id,
            value=website,
            type="website",
            party="winner",
            source=source,
            confidence=0.8,
            snippet=inn,
        ):
            added += 1
        for phone in phones or []:
            if add_contact_if_new(
                conn,
                lot_id,
                value=phone,
                type="phone",
                party="winner",
                source=source,
                confidence=0.75,
                snippet=inn,
            ):
                added += 1
        for mail in emails or []:
            clean = normalize_email(str(mail))
            if not clean:
                continue
            if add_contact_if_new(
                conn,
                lot_id,
                value=clean,
                type="email",
                party="winner",
                source=source,
                confidence=0.75,
                snippet=inn,
            ):
                added += 1
    return added


def add_document_if_new(
    conn: sqlite3.Connection,
    lot_id: int,
    *,
    url: str,
    filename: str | None = None,
) -> bool:
    found = conn.execute(
        "SELECT 1 FROM documents WHERE lot_id = ? AND url = ? LIMIT 1",
        (lot_id, url),
    ).fetchone()
    if found:
        return False
    conn.execute(
        """
        INSERT INTO documents (lot_id, url, filename, ocr_status)
        VALUES (?, ?, ?, 'pending')
        """,
        (lot_id, url, filename),
    )
    return True


def lots_without_documents(
    conn: sqlite3.Connection, limit: int, lot_id: int | None = None
) -> list[sqlite3.Row]:
    if lot_id is not None:
        return conn.execute(
            """
            SELECT id, external_id
            FROM lots
            WHERE id = ?
              AND source LIKE '%eis%'
            """,
            (lot_id,),
        ).fetchall()
    return conn.execute(
        """
        SELECT id, external_id
        FROM lots
        WHERE source LIKE '%eis%'
          AND id NOT IN (SELECT DISTINCT lot_id FROM documents WHERE lot_id IS NOT NULL)
        ORDER BY amount_rub DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def pending_documents(
    conn: sqlite3.Connection, limit: int, lot_id: int | None = None
) -> list[sqlite3.Row]:
    if lot_id is not None:
        return conn.execute(
            """
            SELECT d.id, d.lot_id, d.url, d.filename, l.external_id,
                   l.winner_name, l.customer_name, l.winner_inn, l.customer_inn
            FROM documents d
            JOIN lots l ON l.id = d.lot_id
            WHERE d.ocr_status = 'pending' AND d.lot_id = ?
            ORDER BY d.id
            LIMIT ?
            """,
            (lot_id, limit),
        ).fetchall()
    return conn.execute(
        """
            SELECT d.id, d.lot_id, d.url, d.filename, l.external_id,
                   l.winner_name, l.customer_name, l.winner_inn, l.customer_inn
            FROM documents d
            JOIN lots l ON l.id = d.lot_id
            WHERE d.ocr_status = 'pending'
        ORDER BY CASE
            WHEN EXISTS (
                SELECT 1 FROM contacts c WHERE c.lot_id = d.lot_id AND c.type = 'phone'
            ) THEN 1 ELSE 0
        END, l.amount_rub DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def update_document(
    conn: sqlite3.Connection,
    doc_id: int,
    *,
    local_path: str | None = None,
    mime: str | None = None,
    ocr_status: str,
    ocr_text: str | None = None,
    ocr_summary: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE documents
        SET local_path = COALESCE(?, local_path),
            mime = COALESCE(?, mime),
            ocr_status = ?,
            ocr_text = COALESCE(?, ocr_text),
            ocr_summary = COALESCE(?, ocr_summary)
        WHERE id = ?
        """,
        (local_path, mime, ocr_status, ocr_text, ocr_summary, doc_id),
    )


def purge_fake_phones(conn: sqlite3.Connection) -> int:
    from app.htmlutil import is_ru_phone

    rows = conn.execute(
        "SELECT id, value FROM contacts WHERE type = 'phone'"
    ).fetchall()
    dropped = 0
    for row in rows:
        if not is_ru_phone(str(row["value"])):
            conn.execute("DELETE FROM contacts WHERE id = ?", (row["id"],))
            dropped += 1
    return dropped


def purge_junk_websites(conn: sqlite3.Connection) -> int:
    from app.htmlutil import is_junk_website, normalize_website

    rows = conn.execute(
        "SELECT id, value FROM contacts WHERE type = 'website'"
    ).fetchall()
    dropped = 0
    for row in rows:
        raw = str(row["value"])
        if is_junk_website(raw) or not normalize_website(raw):
            conn.execute("DELETE FROM contacts WHERE id = ?", (row["id"],))
            dropped += 1
    return dropped


def purge_junk_emails(conn: sqlite3.Connection) -> int:
    from app.htmlutil import normalize_email

    rows = conn.execute("SELECT id, value FROM contacts WHERE type = 'email'").fetchall()
    dropped = 0
    for row in rows:
        if not normalize_email(str(row["value"])):
            conn.execute("DELETE FROM contacts WHERE id = ?", (row["id"],))
            dropped += 1
    for row in conn.execute(
        "SELECT inn, source, payload FROM org_cache WHERE IFNULL(payload, '') != ''"
    ):
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        mails = payload.get("emails")
        if not isinstance(mails, list):
            continue
        cleaned = [item for item in mails if isinstance(item, str) and normalize_email(item)]
        if cleaned == mails:
            continue
        payload["emails"] = cleaned
        conn.execute(
            "UPDATE org_cache SET payload = ? WHERE inn = ? AND source = ?",
            (json.dumps(payload, ensure_ascii=False), row["inn"], row["source"]),
        )
    return dropped


def upsert_org_cache(
    conn: sqlite3.Connection,
    inn: str,
    *,
    name: str | None = None,
    status: str | None = None,
    payload: str | None = None,
    source: str | None = None,
    ogrn: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO org_cache (inn, source, ogrn, name, status, payload, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(inn, source) DO UPDATE SET
            ogrn = COALESCE(excluded.ogrn, org_cache.ogrn),
            name = COALESCE(excluded.name, org_cache.name),
            status = COALESCE(excluded.status, org_cache.status),
            payload = COALESCE(excluded.payload, org_cache.payload),
            updated_at = excluded.updated_at
        """,
        (inn, source or "", ogrn, name, status, payload, utc_now()),
    )


def org_cache_fresh(conn: sqlite3.Connection, inn: str, source: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM org_cache WHERE inn = ? AND source = ? LIMIT 1",
        (inn, source),
    ).fetchone()
    return row is not None


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def get_org(conn: sqlite3.Connection, inn: str, source: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM org_cache WHERE inn = ? AND source = ? LIMIT 1",
        (inn, source),
    ).fetchone()


def set_lot_status(conn: sqlite3.Connection, lot_id: int, status: str) -> bool:
    cur = conn.execute(
        "UPDATE lots SET status = ?, updated_at = ? WHERE id = ?",
        (status, utc_now(), lot_id),
    )
    return cur.rowcount > 0


def set_lot_analysis(conn: sqlite3.Connection, lot_id: int, payload: str) -> None:
    conn.execute(
        "UPDATE lots SET lead_analysis = ?, updated_at = ? WHERE id = ?",
        (payload, utc_now(), lot_id),
    )
