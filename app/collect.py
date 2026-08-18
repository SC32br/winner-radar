"""CLI сбора: ЕИС + mos.ru + префильтр + запись в SQLite."""

from __future__ import annotations

import argparse

from app import config, db, pipeline
from app.filters import effective_filters
from app.logutil import LotLog, setup_logging


def collect(
    *,
    skip_mos: bool = False,
    skip_eis: bool = False,
    skip_cs: bool = False,
    skip_docs: bool = False,
    skip_sites: bool = False,
    sites_only: bool = False,
    docs_only: bool = False,
    lot_id: int | None = None,
    full_pass: bool = False,
    from_date: str | None = None,
    limit: int | None = None,
    year: str | None = None,
    skip_checko: bool = False,
    daily: bool = False,
) -> int:
    log = LotLog(setup_logging(), {"external_id": "-"})
    log.info("collect start db=%s", config.DB_PATH)
    conn = db.connect()
    try:
        db.init_schema(conn)
        filters = effective_filters(conn)
        db.upsert_setting(conn, "amount_min", str(filters["amount_min"]))
        db.upsert_setting(conn, "region_code", str(filters["region_code"]))
        conn.commit()
        collect_cfg = dict(filters.get("collect") or {})
        if skip_checko:
            collect_cfg["skip_checko"] = True
        filters = dict(filters)
        filters["collect"] = collect_cfg
        if year:
            collect_cfg["max_docs"] = max(int(collect_cfg.get("max_docs") or 24), 40)
            collect_cfg["max_doc_lists"] = max(int(collect_cfg.get("max_doc_lists") or 6), 1)
            collect_cfg["max_ocr_pages"] = max(int(collect_cfg.get("max_ocr_pages") or 6), 8)
            collect_cfg["skip_checko"] = True
            stats = pipeline.year_pass(conn, log, collect_cfg, year=str(year))
            conn.commit()
            log.info("year-pass stats=%s", {k: stats.get(k) for k in ("year", "total", "done", "skipped", "errors")})
        elif lot_id is not None:
            collect_cfg["max_docs"] = max(int(collect_cfg.get("max_docs") or 24), 40)
            collect_cfg["max_doc_lists"] = max(int(collect_cfg.get("max_doc_lists") or 6), 1)
            collect_cfg["max_ocr_pages"] = max(int(collect_cfg.get("max_ocr_pages") or 6), 8)
            stats = pipeline.enrich_one_lot(conn, log, collect_cfg, lot_id)
            conn.commit()
            log.info("lot-id=%s stats=%s", lot_id, stats)
        elif daily:
            stats = pipeline.daily_pass(conn, log, filters)
            conn.commit()
            listing = stats.get("listing") or {}
            log.info(
                "daily-pass window=%s seen=%s created=%s enriched=%s errors=%s",
                stats.get("from_date"),
                listing.get("seen"),
                listing.get("created"),
                stats.get("enriched"),
                stats.get("errors"),
            )
        elif full_pass:
            collect_cfg["max_docs"] = max(int(collect_cfg.get("max_docs") or 24), 40)
            collect_cfg["max_doc_lists"] = max(int(collect_cfg.get("max_doc_lists") or 6), 1)
            collect_cfg["max_ocr_pages"] = max(int(collect_cfg.get("max_ocr_pages") or 6), 8)
            stats = pipeline.full_pass(
                conn,
                log,
                collect_cfg,
                from_date=from_date or "2025-01-01",
                limit=int(limit or 90),
            )
            conn.commit()
            log.info("full-pass stats=%s", {k: stats.get(k) for k in ("total", "done", "skipped", "errors")})
        elif docs_only:
            stats = pipeline.enrich_documents(conn, log, collect_cfg)
            conn.commit()
            log.info(
                "docs-only listed=%s done=%s contacts=%s skipped=%s errors=%s summaries=%s leads=%s",
                stats.get("listed"),
                stats.get("done"),
                stats.get("contacts"),
                stats.get("skipped"),
                stats.get("errors"),
                stats.get("summaries"),
                stats.get("leads"),
            )
        elif sites_only:
            found = pipeline.enrich_winner_sites(conn, log, collect_cfg)
            conn.commit()
            log.info("sites-only done found=%s", found)
        else:
            stats = pipeline.run(
                conn,
                log,
                filters,
                skip_mos=skip_mos,
                skip_eis=skip_eis,
                skip_cs=skip_cs,
                skip_docs=skip_docs,
                skip_sites=skip_sites,
            )
            conn.commit()
            log.info(
                "collect done seen=%s filtered=%s cards=%s stored=%s created=%s cs=%s sites=%s docs=%s errors=%s",
                stats["seen"],
                stats["filtered"],
                stats["cards"],
                stats["stored"],
                stats["created"],
                stats["cs"],
                stats["sites"],
                stats["docs"],
                stats["errors"],
            )
    finally:
        conn.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Сбор лотов радара заказов")
    parser.add_argument("--skip-mos", action="store_true", help="не ходить на mos.ru")
    parser.add_argument("--skip-eis", action="store_true", help="не ходить на ЕИС")
    parser.add_argument("--skip-cs", action="store_true", help="не ходить в ClearSpending")
    parser.add_argument("--skip-docs", action="store_true", help="не качать список файлов с карточки")
    parser.add_argument("--skip-sites", action="store_true", help="не искать сайты победителей по ИНН")
    parser.add_argument("--sites-only", action="store_true", help="только добор сайтов по ИНН, без ленты ЕИС")
    parser.add_argument("--docs-only", action="store_true", help="скачать документы лотов и достать контакты OCR")
    parser.add_argument("--lot-id", type=int, default=None, help="эталон: контакты+доки+разбор одного лота")
    parser.add_argument("--full-pass", action="store_true", help="полная проходка карточек: сайт, доки, разбор")
    parser.add_argument("--from-date", default=None, help="карточки с этой даты создания, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None, help="сколько карточек в проходке")
    parser.add_argument("--year", default=None, help="полная доборка карточек года контракта, без новых запросов Checko")
    parser.add_argument("--daily", action="store_true", help="утренний сбор ЕИС/mos за 3 дня и полный прогон новых")
    parser.add_argument("--skip-checko", action="store_true", help="не тратить лимит Checko, кэш Checko можно использовать")
    args = parser.parse_args()
    raise SystemExit(
        collect(
            skip_mos=args.skip_mos,
            skip_eis=args.skip_eis,
            skip_cs=args.skip_cs,
            skip_docs=args.skip_docs,
            skip_sites=args.skip_sites,
            sites_only=args.sites_only,
            docs_only=args.docs_only,
            lot_id=args.lot_id,
            full_pass=args.full_pass,
            from_date=args.from_date,
            limit=args.limit,
            year=args.year,
            skip_checko=args.skip_checko,
            daily=args.daily,
        )
    )


if __name__ == "__main__":
    main()
