"""Сбор: лента → префильтр → карточка → дедуп → ClearSpending по ИНН победителя."""

from __future__ import annotations

import json
from typing import Any

from app import db
from app.collectors.checko import dumps as checko_dumps
from app.collectors.checko import profile as checko_profile
from app.collectors.winner_site import (
    emails_for_winner,
    probe_winner_site,
    scrape_verified_site,
    websites_for_winner,
    winner_name_for_inn,
)
from app.collectors.clearspending import dumps as cs_dumps
from app.collectors.clearspending import profile as cs_profile
from app.collectors.eis import EisCollector
from app.collectors.mos import MosCollector
from app.docs import enrich_documents
from app.http_client import FetchError, Http, eis_http, json_http
from app.logutil import LotLog
from app.models import ListingHit, LotDraft
from app.prefilter import decide, profiles_for
from app.regions import apply_region, collect_window, date_ok, eis_place_cfg, today_ru


def _reason(hits: list[str], amount: int | None) -> str:
    phrase = hits[0] if hits else "ОКПД"
    if amount is None:
        return f"Попало по фразе «{phrase}»"
    return f"Попало по фразе «{phrase}», сумма {amount:,} ₽".replace(",", " ")


def _lot_row(draft: LotDraft) -> dict[str, Any]:
    return {
        "external_id": draft.external_id,
        "source": draft.source,
        "url": draft.url,
        "subject": draft.subject,
        "amount_rub": draft.amount_rub,
        "region_code": draft.region_code,
        "region_text": draft.region_text,
        "published_at": draft.published_at,
        "signed_at": draft.signed_at,
        "fz": draft.fz,
        "okpd_codes": draft.okpd_codes,
        "matched_keywords": json.dumps(draft.matched_keywords, ensure_ascii=False),
        "customer_name": draft.customer_name,
        "customer_inn": draft.customer_inn,
        "winner_name": draft.winner_name,
        "winner_inn": draft.winner_inn,
        "winner_status": draft.winner_status,
        "profiles": json.dumps(draft.profiles, ensure_ascii=False),
        "reason": draft.reason,
    }


def _store_draft(conn, draft: LotDraft, log: LotLog) -> tuple[int, bool]:
    lot_id, created = db.upsert_lot(conn, _lot_row(draft))
    for contact in draft.contacts:
        db.add_contact_if_new(
            conn,
            lot_id,
            value=contact.value,
            type=contact.type,
            party=contact.party,
            source=contact.source,
            confidence=contact.confidence,
            snippet=contact.snippet[:500] if contact.snippet else None,
        )
    for doc in draft.attachments:
        db.add_document_if_new(conn, lot_id, url=doc.url, filename=doc.filename)
    db.add_event(
        conn,
        "collected" if created else "updated",
        external_id=draft.external_id,
        lot_id=lot_id,
        payload=draft.reason,
    )
    log.info(
        "lot %s winner=%s amount=%s keys=%s",
        "new" if created else "upd",
        draft.winner_inn or "-",
        draft.amount_rub,
        ",".join(draft.matched_keywords[:4]) or "-",
    )
    return lot_id, created


def _eis_hits(
    eis: EisCollector,
    filters: dict[str, Any],
    log: LotLog,
    *,
    region_pages: int,
    listing_pages: int,
) -> list[ListingHit]:
    merged: dict[str, ListingHit] = {}
    queries: list[tuple[str, int]] = [("", page) for page in range(1, region_pages + 1)]
    keywords = [
        str(k)
        for k in (filters.get("harvest_keywords") or filters.get("keywords") or [])
    ]
    for keyword in keywords:
        for page in range(1, listing_pages + 1):
            queries.append((keyword, page))
    for query, page in queries:
        qlog = LotLog(log.logger, {"external_id": query or f"region-p{page}"})
        try:
            hits = eis.fetch_listing(query, page)
        except FetchError as exc:
            qlog.info("лента ЕИС ошибка %s", exc)
            continue
        qlog.info("лента ЕИС page=%s hits=%s q=%r", page, len(hits), query)
        for hit in hits:
            prev = merged.get(hit.external_id)
            if prev is None:
                merged[hit.external_id] = hit
            elif len(hit.subject) > len(prev.subject):
                merged[hit.external_id] = hit
    return list(merged.values())


def _mos_hits(
    mos: MosCollector,
    filters: dict[str, Any],
    log: LotLog,
    *,
    listing_pages: int,
) -> list[ListingHit]:
    merged: dict[str, ListingHit] = {}
    harvest = [
        str(k)
        for k in (filters.get("harvest_keywords") or filters.get("keywords") or [])
        if str(k).strip()
    ]
    pages = max(1, int(listing_pages or 1))
    for query in harvest:
        for page in range(1, pages + 1):
            qlog = LotLog(log.logger, {"external_id": f"mos:{query}"})
            try:
                hits = mos.fetch_listing(query, page)
            except FetchError as exc:
                qlog.info("mos.ru ошибка %s", exc)
                continue
            qlog.info("mos.ru page=%s hits=%s (победитель+гео) q=%r", page, len(hits), query)
            for hit in hits:
                merged.setdefault(hit.external_id, hit)
    return list(merged.values())


def run(
    conn,
    log: LotLog,
    filters: dict[str, Any],
    *,
    skip_mos: bool = False,
    skip_eis: bool = False,
    skip_cs: bool = False,
    skip_docs: bool = False,
    skip_sites: bool = False,
) -> dict[str, int]:
    collect_cfg = filters.get("collect") or {}
    sleep_sec = float(collect_cfg.get("sleep_sec") or 0.35)
    region_pages = int(collect_cfg.get("region_pages") or 3)
    listing_pages = int(collect_cfg.get("listing_pages") or 1)
    max_cards = int(collect_cfg.get("max_cards") or 80)
    max_cs = int(collect_cfg.get("max_cs") or 40)
    records = str(collect_cfg.get("records_per_page") or "_50")
    cutoff, date_from_ru = collect_window(collect_cfg)
    date_to = today_ru()
    amount_min = int(filters.get("amount_min") or 500000)

    stats = {
        "seen": 0,
        "filtered": 0,
        "cards": 0,
        "stored": 0,
        "created": 0,
        "cs": 0,
        "sites": 0,
        "docs": 0,
        "errors": 0,
    }

    eis_client = eis_http(sleep_sec)
    mos_client = json_http(sleep_sec, verify=False)
    cs_client = json_http(0.4, verify=True, timeout=8.0)
    try:
        log.info(
            "окно %s…%s регионы=%s",
            cutoff,
            date_to,
            ",".join(item["code"] for item in (filters.get("regions") or [])) or "35",
        )
        eis = EisCollector(
            eis_client,
            eis_place_cfg(filters),
            records,
            date_from_ru=date_from_ru,
            date_to_ru=date_to,
            price_from=amount_min,
        )
        hits: list[ListingHit] = []
        if not skip_eis:
            hits = _eis_hits(
                eis,
                filters,
                log,
                region_pages=region_pages,
                listing_pages=listing_pages,
            )
        mos_col = MosCollector(mos_client, list(filters.get("geo_words") or []))
        if not skip_mos:
            for hit in _mos_hits(mos_col, filters, log, listing_pages=listing_pages):
                if hit.external_id not in {item.external_id for item in hits}:
                    hits.append(hit)
                else:
                    for item in hits:
                        if item.external_id == hit.external_id and "mos" not in item.source:
                            item.source = f"{item.source},mos"
                            break

        stats["seen"] = len(hits)
        passed: list[ListingHit] = []
        for hit in hits:
            item_log = LotLog(log.logger, {"external_id": hit.external_id})
            hit.extra = apply_region(
                f"{hit.customer_name} {hit.subject} {(hit.extra or {}).get('region_name') or ''}",
                filters,
                hit.extra,
                inn=hit.customer_inn,
            )
            if not date_ok(hit.signed_at, hit.published_at, cutoff):
                stats["filtered"] += 1
                item_log.info("отсев older_than_%s", cutoff)
                continue
            ok, why, keys = decide(
                subject=hit.subject,
                amount_rub=hit.amount_rub,
                customer_inn=hit.customer_inn,
                winner_inn=(hit.extra or {}).get("winner_inn"),
                filters=filters,
            )
            if not ok:
                stats["filtered"] += 1
                item_log.info("отсев %s", why)
                if why != "no_keyword":
                    db.add_event(
                        conn,
                        "filtered_out",
                        external_id=hit.external_id,
                        payload=why,
                    )
                continue
            hit.extra = {**(hit.extra or {}), "matched": keys}
            passed.append(hit)

        passed.sort(key=lambda item: item.amount_rub or 0, reverse=True)
        cards_left = max_cards
        cs_left = 0 if skip_cs else max_cs
        for hit in passed:
            if cards_left <= 0:
                break
            item_log = LotLog(log.logger, {"external_id": hit.external_id})
            try:
                if hit.source.startswith("mos") and not hit.source.startswith("eis"):
                    draft = mos_col.fetch_card(hit)
                    attachments = []
                else:
                    draft = eis.fetch_card(hit)
                    attachments = [] if skip_docs else eis.fetch_attachments(hit)
            except FetchError as exc:
                stats["errors"] += 1
                item_log.info("карточка ошибка %s", exc)
                continue
            cards_left -= 1
            stats["cards"] += 1
            if draft is None:
                stats["filtered"] += 1
                item_log.info("карточка пустая")
                continue
            if not (draft.winner_name or "").strip() or not draft.winner_inn:
                stats["filtered"] += 1
                item_log.info("отсев no_winner")
                continue
            extra = apply_region(
                " ".join(
                    [
                        draft.customer_name,
                        draft.subject,
                        draft.winner_name,
                        str((hit.extra or {}).get("region_name") or ""),
                    ]
                ),
                filters,
                hit.extra,
                inn=draft.customer_inn or hit.customer_inn,
            )
            hit.extra = extra
            draft.region_code = str(extra.get("region_code") or "")
            draft.region_text = str(extra.get("region_text") or "")
            if not date_ok(draft.signed_at, draft.published_at, cutoff):
                stats["filtered"] += 1
                item_log.info("карточка отсев older_than_%s", cutoff)
                continue
            if attachments:
                draft.attachments = attachments
            ok, why, keys = decide(
                subject=draft.subject or hit.subject,
                amount_rub=draft.amount_rub,
                customer_inn=draft.customer_inn,
                winner_inn=draft.winner_inn,
                okpd_codes=draft.okpd_codes,
                filters=filters,
            )
            if not ok:
                stats["filtered"] += 1
                db.add_event(
                    conn,
                    "filtered_out",
                    external_id=hit.external_id,
                    payload=f"card:{why}",
                )
                item_log.info("карточка отсев %s", why)
                continue
            draft.matched_keywords = keys or list((hit.extra or {}).get("matched") or [])
            draft.profiles = profiles_for(draft.matched_keywords)
            draft.reason = _reason(draft.matched_keywords, draft.amount_rub)
            if "mos" in hit.source and "mos" not in draft.source:
                draft.source = f"{draft.source},mos"
            lot_id, created = _store_draft(conn, draft, item_log)
            stats["stored"] += 1
            if created:
                stats["created"] += 1
            inn = draft.winner_inn
            if inn and cs_left > 0 and not db.org_cache_fresh(conn, inn, "clearspending"):
                payload = cs_profile(cs_client, inn)
                cs_left -= 1
                stats["cs"] += 1
                if payload:
                    db.upsert_org_cache(
                        conn,
                        inn,
                        name=draft.winner_name or None,
                        payload=cs_dumps(payload),
                        source="clearspending",
                    )
                    item_log.info("clearspending total=%s", payload.get("total"))
            conn.commit()
    finally:
        eis_client.close()
        mos_client.close()
        cs_client.close()
    if not skip_sites:
        stats["sites"] = enrich_winner_sites(conn, log, collect_cfg)
    if not skip_docs:
        doc_stats = enrich_documents(conn, log, collect_cfg)
        stats["docs"] = int(doc_stats.get("done") or 0)
    return stats


def enrich_winner_sites(
    conn, log: LotLog, collect_cfg: dict[str, Any], inn: str | None = None
) -> int:
    max_sites = int(collect_cfg.get("max_sites") or 80)
    sleep_sec = float(collect_cfg.get("sleep_sec") or 0.35)
    skip_checko = bool(collect_cfg.get("skip_checko"))
    found = 0
    found += _enrich_sites_from_web(
        conn, log, max_sites=max_sites, sleep_sec=sleep_sec, inn=inn
    )
    found += _enrich_checko(
        conn,
        log,
        max_sites=max_sites,
        sleep_sec=sleep_sec,
        inn=inn,
        skip_api=skip_checko,
    )
    found += _enrich_dadata(conn, log, inn=inn)
    return found


def _probe_inn_site(conn, client: Http, insecure: Http, inn: str) -> dict[str, Any] | None:
    name = winner_name_for_inn(conn, inn)
    for email in emails_for_winner(conn, inn):
        hit = probe_winner_site(client, email, insecure, inn=inn, name=name)
        if hit:
            return hit
    for url in websites_for_winner(conn, inn):
        hit = scrape_verified_site(client, url, insecure, inn=inn, name=name)
        if hit:
            hit["from_email"] = None
            return hit
    return None


def _enrich_sites_from_web(
    conn, log: LotLog, *, max_sites: int, sleep_sec: float, inn: str | None = None
) -> int:
    inns = [inn] if inn else db.winner_inns_missing_source(conn, "winner_site")
    inns = [item for item in inns if item]
    if not inns or max_sites <= 0:
        return 0
    found = 0
    client = Http(verify=True, sleep_sec=max(0.15, min(sleep_sec, 0.25)), timeout=8.0)
    insecure = Http(verify=False, sleep_sec=max(0.15, min(sleep_sec, 0.25)), timeout=8.0)
    try:
        for item in inns[:max_sites]:
            item_log = LotLog(log.logger, {"external_id": item})
            hit = _probe_inn_site(conn, client, insecure, item)
            if not hit:
                continue
            attached = db.attach_winner_contacts(
                conn,
                item,
                website=hit.get("website"),
                phones=list(hit.get("phones") or []),
                emails=list(hit.get("emails") or []),
                source="winner_site",
            )
            found += 1
            conn.commit()
            item_log.info(
                "сайт фирмы %s → %s phones=%s emails=%s attached=%s",
                hit.get("from_email") or "url",
                hit.get("website"),
                len(hit.get("phones") or []),
                len(hit.get("emails") or []),
                attached,
            )
    finally:
        client.close()
        insecure.close()
    return found


def _enrich_checko(
    conn, log: LotLog, *, max_sites: int, sleep_sec: float, inn: str | None = None, skip_api: bool = False
) -> int:
    inns = [inn] if inn else db.winner_inns_missing_source(conn, "checko")
    inns = [item for item in inns if item]
    if not inns or max_sites <= 0:
        return 0
    found = 0
    client = json_http(max(0.45, sleep_sec), verify=True, timeout=20.0)
    try:
        for item in inns[:max_sites]:
            item_log = LotLog(log.logger, {"external_id": item})
            payload = None
            if db.org_cache_fresh(conn, item, "checko"):
                row = db.get_org(conn, item, "checko")
                if row and row["payload"]:
                    try:
                        payload = json.loads(row["payload"])
                    except json.JSONDecodeError:
                        payload = None
            if not isinstance(payload, dict) or "phones" not in payload:
                if skip_api:
                    continue
                payload = checko_profile(client, item)
                if payload is None:
                    item_log.info("checko не ответил")
                    continue
                if payload.get("error") == "rate_limit":
                    item_log.info("checko лимит, добор останавливаем")
                    break
                db.upsert_org_cache(conn, item, payload=checko_dumps(payload), source="checko")
            attached = db.attach_winner_contacts(
                conn,
                item,
                website=payload.get("website"),
                phones=list(payload.get("phones") or []),
                emails=list(payload.get("emails") or []),
                source="checko",
            )
            if attached:
                found += 1
            item_log.info(
                "checko сайт=%s phones=%s emails=%s attached=%s",
                payload.get("website") or "-",
                len(payload.get("phones") or []),
                len(payload.get("emails") or []),
                attached,
            )
            conn.commit()
    finally:
        client.close()
    return found


def _enrich_dadata(conn, log: LotLog, inn: str | None = None) -> int:
    from app import dadata

    inns = [inn] if inn else db.winner_inns_missing_source(conn, "dadata")
    inns = [item for item in inns if item][:40]
    found = 0
    for item in inns:
        parsed = dadata.cached(conn, item) or dadata.fetch_and_store(conn, item)
        if not parsed:
            continue
        attached = db.attach_winner_contacts(
            conn,
            item,
            website=parsed.get("website"),
            phones=list(parsed.get("phones") or []),
            emails=list(parsed.get("emails") or []),
            source="dadata",
        )
        if attached:
            found += 1
            conn.commit()
    if found:
        log.info("dadata контактов по фирмам=%s", found)
    return found


def enrich_one_lot(conn, log: LotLog, collect_cfg: dict[str, Any], lot_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, winner_inn, external_id FROM lots WHERE id = ?",
        (lot_id,),
    ).fetchone()
    if row is None:
        return {"error": "lot not found"}
    inn = str(row["winner_inn"] or "")
    item_log = LotLog(log.logger, {"external_id": row["external_id"]})
    sites = 0
    if inn:
        sites = enrich_winner_sites(conn, item_log, collect_cfg, inn=inn)
    docs = enrich_documents(conn, item_log, collect_cfg, lot_id=lot_id)
    conn.commit()
    return {"lot_id": lot_id, "inn": inn, "sites": sites, "docs": docs}


def daily_window_iso(days: int = 3) -> str:
    from datetime import date, timedelta

    return (date.today() - timedelta(days=max(1, int(days)))).isoformat()


def lots_thin_since(conn, from_iso: str) -> list[int]:
    rows = conn.execute(
        """
        SELECT l.id
        FROM lots l
        WHERE IFNULL(l.status, 'new') != 'reject'
          AND l.created_at >= ?
          AND (
            NOT EXISTS (SELECT 1 FROM documents d WHERE d.lot_id = l.id)
            OR l.lead_analysis IS NULL
            OR length(l.lead_analysis) < 20
          )
        ORDER BY l.id
        """,
        (from_iso,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def daily_pass(
    conn,
    log: LotLog,
    filters: dict[str, Any],
    *,
    days: int = 3,
    progress_path: Any = None,
) -> dict[str, Any]:
    from datetime import date
    from pathlib import Path

    from app import config

    from_iso = daily_window_iso(days)
    today_iso = date.today().isoformat()
    collect_cfg = dict(filters.get("collect") or {})
    collect_cfg["date_from"] = from_iso
    collect_cfg["max_docs"] = max(int(collect_cfg.get("max_docs") or 24), 40)
    collect_cfg["max_doc_lists"] = max(int(collect_cfg.get("max_doc_lists") or 6), 1)
    collect_cfg["max_ocr_pages"] = max(int(collect_cfg.get("max_ocr_pages") or 6), 8)
    filters = dict(filters)
    filters["collect"] = collect_cfg
    before_ids = {int(row[0]) for row in conn.execute("SELECT id FROM lots").fetchall()}
    listing = run(
        conn,
        log,
        filters,
        skip_mos=False,
        skip_eis=False,
        skip_cs=False,
        skip_docs=True,
        skip_sites=True,
    )
    after_ids = {int(row[0]) for row in conn.execute("SELECT id FROM lots").fetchall()}
    new_ids = sorted(after_ids - before_ids)
    enrich_ids = sorted(set(new_ids) | set(lots_thin_since(conn, today_iso)))
    path = Path(progress_path) if progress_path else config.LOG_DIR / "radar_daily.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {
        "from_date": from_iso,
        "enrich_from": today_iso,
        "days": int(days),
        "listing": listing,
        "new_ids": new_ids,
        "enrich_ids": enrich_ids,
        "enriched": 0,
        "errors": 0,
        "last_lot_id": None,
    }

    def _write() -> None:
        path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    _write()
    log.info(
        "daily start window=%s seen=%s created=%s enrich=%s",
        from_iso,
        listing.get("seen"),
        listing.get("created"),
        enrich_ids,
    )
    for lot_id in enrich_ids:
        log.info("daily enrich lot=%s", lot_id)
        try:
            enrich_one_lot(conn, log, collect_cfg, lot_id)
        except Exception as exc:
            stats["errors"] += 1
            log.info("daily lot=%s error %s", lot_id, exc)
            conn.commit()
            _write()
            continue
        stats["enriched"] += 1
        stats["last_lot_id"] = lot_id
        conn.commit()
        _write()
    log.info(
        "daily done created=%s enriched=%s errors=%s window=%s",
        len(new_ids),
        stats["enriched"],
        stats["errors"],
        from_iso,
    )
    return stats


def lots_for_year_pass(conn, *, year: str) -> list[Any]:
    return conn.execute(
        """
        SELECT id, external_id, amount_rub, subject
        FROM lots
        WHERE IFNULL(status, 'new') != 'reject'
          AND substr(COALESCE(signed_at, published_at, ''), 1, 4) = ?
        ORDER BY COALESCE(amount_rub, 0) DESC, id DESC
        """,
        (str(year),),
    ).fetchall()


def lots_for_full_pass(
    conn, *, from_date: str, limit: int
) -> list[Any]:
    return conn.execute(
        """
        SELECT id, external_id, amount_rub, subject
        FROM lots
        WHERE IFNULL(status, 'new') != 'reject'
          AND created_at >= ?
        ORDER BY COALESCE(amount_rub, 0) DESC, id DESC
        LIMIT ?
        """,
        (from_date, max(1, int(limit))),
    ).fetchall()


def full_pass(
    conn,
    log: LotLog,
    collect_cfg: dict[str, Any],
    *,
    from_date: str,
    limit: int,
    progress_path: Any = None,
) -> dict[str, Any]:
    from pathlib import Path

    from app import config

    rows = lots_for_full_pass(conn, from_date=from_date, limit=limit)
    path = Path(progress_path) if progress_path else config.LOG_DIR / "full_pass.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    done_ids: set[int] = set()
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            if prev.get("from_date") == from_date and int(prev.get("limit") or 0) == int(limit):
                done_ids = {int(x) for x in (prev.get("done_ids") or [])}
        except (json.JSONDecodeError, TypeError, ValueError):
            done_ids = set()
    stats = {
        "total": len(rows),
        "done": 0,
        "skipped": 0,
        "errors": 0,
        "from_date": from_date,
        "limit": int(limit),
        "done_ids": sorted(done_ids),
    }

    def _write() -> None:
        path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    _write()
    log.info("full-pass start total=%s from=%s limit=%s already=%s", len(rows), from_date, limit, len(done_ids))
    for index, row in enumerate(rows, start=1):
        lot_id = int(row["id"])
        if lot_id in done_ids:
            stats["skipped"] += 1
            log.info("full-pass %s/%s lot=%s skip", index, len(rows), lot_id)
            continue
        log.info(
            "full-pass %s/%s lot=%s amount=%s",
            index,
            len(rows),
            lot_id,
            row["amount_rub"],
        )
        try:
            enrich_one_lot(conn, log, collect_cfg, lot_id)
        except Exception as exc:
            stats["errors"] += 1
            log.info("full-pass lot=%s error %s", lot_id, exc)
            conn.commit()
            _write()
            continue
        done_ids.add(lot_id)
        stats["done"] += 1
        stats["done_ids"] = sorted(done_ids)
        stats["last_lot_id"] = lot_id
        conn.commit()
        _write()
    log.info(
        "full-pass done total=%s new=%s skipped=%s errors=%s",
        stats["total"],
        stats["done"],
        stats["skipped"],
        stats["errors"],
    )
    return stats


def year_pass(
    conn,
    log: LotLog,
    collect_cfg: dict[str, Any],
    *,
    year: str,
    progress_path: Any = None,
) -> dict[str, Any]:
    from pathlib import Path

    from app import config

    collect_cfg = dict(collect_cfg)
    collect_cfg["skip_checko"] = True
    collect_cfg["max_docs"] = max(int(collect_cfg.get("max_docs") or 24), 40)
    collect_cfg["max_doc_lists"] = max(int(collect_cfg.get("max_doc_lists") or 6), 1)
    collect_cfg["max_ocr_pages"] = max(int(collect_cfg.get("max_ocr_pages") or 6), 8)
    rows = lots_for_year_pass(conn, year=year)
    path = Path(progress_path) if progress_path else config.LOG_DIR / f"year_{year}_no_checko.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    done_ids: set[int] = set()
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            if str(prev.get("year") or "") == str(year):
                done_ids = {int(x) for x in (prev.get("done_ids") or [])}
        except (json.JSONDecodeError, TypeError, ValueError):
            done_ids = set()
    stats = {
        "total": len(rows),
        "done": 0,
        "skipped": 0,
        "errors": 0,
        "year": str(year),
        "skip_checko": True,
        "done_ids": sorted(done_ids),
    }

    def _write() -> None:
        path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    _write()
    log.info(
        "year-pass start year=%s total=%s already=%s checko=off",
        year,
        len(rows),
        len(done_ids),
    )
    for index, row in enumerate(rows, start=1):
        lot_id = int(row["id"])
        if lot_id in done_ids:
            stats["skipped"] += 1
            continue
        log.info(
            "year-pass %s/%s lot=%s amount=%s",
            index,
            len(rows),
            lot_id,
            row["amount_rub"],
        )
        try:
            enrich_one_lot(conn, log, collect_cfg, lot_id)
        except Exception as exc:
            stats["errors"] += 1
            log.info("year-pass lot=%s error %s", lot_id, exc)
            conn.commit()
            _write()
            continue
        done_ids.add(lot_id)
        stats["done"] += 1
        stats["done_ids"] = sorted(done_ids)
        stats["last_lot_id"] = lot_id
        conn.commit()
        _write()
    log.info(
        "year-pass done year=%s total=%s new=%s skipped=%s errors=%s",
        year,
        stats["total"],
        stats["done"],
        stats["skipped"],
        stats["errors"],
    )
    return stats
