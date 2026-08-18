"""Фейковые лоты, чтобы дашборд не был пустым до первого сбора."""

from __future__ import annotations

import json
import sqlite3

from app import db

# Телефон +70000000000 валидатор выкидывает (слишком много нулей).
# В демо стоит синтетический мобильный, который проходит is_ru_phone.
FAKE_PHONE = "+79001234567"
FAKE_INN = "0000000000"

_LOTS = (
    {
        "external_id": "demo-0000000000000000001",
        "source": "eis",
        "url": "https://zakupki.gov.ru/epz/contract/contractCard/common-info.html?reestrNumber=0000000000000000001",
        "subject": "Строительство здания школы, монолитные работы и устройство фундамента",
        "amount_rub": 12_500_000,
        "currency": "RUB",
        "region_code": "77",
        "region_text": "Москва",
        "published_at": "2026-03-01",
        "signed_at": "2026-03-12",
        "fz": "44",
        "okpd_codes": json.dumps(["41.20"], ensure_ascii=False),
        "matched_keywords": json.dumps(
            ["строительство школы", "монолитные работы", "устройство фундамента"],
            ensure_ascii=False,
        ),
        "customer_name": "ГБОУ «Примерная школа №1»",
        "customer_inn": "7700000000",
        "winner_name": "ООО «Пример Строй»",
        "winner_inn": FAKE_INN,
        "winner_status": "действующая",
        "score": 0.9,
        "profiles": json.dumps(["object", "monolith", "foundation"], ensure_ascii=False),
        "reason": "Ключи совпали с предметом: школа, монолит, фундамент.",
        "status": "new",
        "phone": FAKE_PHONE,
        "email": "info@primer-stroy.example",
        "website": "https://primer-stroy.example",
    },
    {
        "external_id": "demo-0000000000000000002",
        "source": "eis",
        "url": "https://zakupki.gov.ru/epz/contract/contractCard/common-info.html?reestrNumber=0000000000000000002",
        "subject": "Устройство свайного фундамента и ростверка под жилой корпус",
        "amount_rub": 3_200_000,
        "currency": "RUB",
        "region_code": "50",
        "region_text": "Московская область",
        "published_at": "2026-04-02",
        "signed_at": "2026-04-18",
        "fz": "44",
        "okpd_codes": json.dumps(["43.99"], ensure_ascii=False),
        "matched_keywords": json.dumps(
            ["свайный фундамент", "устройство ростверка", "буронабивные сваи"],
            ensure_ascii=False,
        ),
        "customer_name": "ООО «Пример Заказчик»",
        "customer_inn": "5000000000",
        "winner_name": "ООО «Пример Фундамент»",
        "winner_inn": "0000000001",
        "winner_status": "действующая",
        "score": 0.8,
        "profiles": json.dumps(["foundation", "piles"], ensure_ascii=False),
        "reason": "Ключи совпали с предметом: сваи, ростверк.",
        "status": "watching",
        "phone": "+79007654321",
        "email": "hello@primer-fundament.example",
        "website": "https://primer-fundament.example",
    },
    {
        "external_id": "demo-0000000000000000003",
        "source": "mos",
        "url": "https://zakupki.mos.ru/",
        "subject": "Геодезическое сопровождение строительства, разбивка осей, исполнительная съемка",
        "amount_rub": 820_000,
        "currency": "RUB",
        "region_code": "77",
        "region_text": "Москва",
        "published_at": "2026-05-10",
        "signed_at": "2026-05-21",
        "fz": "44",
        "okpd_codes": json.dumps(["71.12"], ensure_ascii=False),
        "matched_keywords": json.dumps(
            ["геодезическое сопровождение", "разбивка осей", "исполнительная съемка"],
            ensure_ascii=False,
        ),
        "customer_name": "ГБУ «Пример Стройзаказчик»",
        "customer_inn": "7700000001",
        "winner_name": "ООО «Пример Гео»",
        "winner_inn": "0000000002",
        "winner_status": "действующая",
        "score": 0.7,
        "profiles": json.dumps(["geodesy"], ensure_ascii=False),
        "reason": "Ключи совпали с предметом: геодезия.",
        "status": "new",
        "phone": "+79005550001",
        "email": "geo@primer-geo.example",
        "website": None,
    },
)


def seed_if_empty(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM lots").fetchone()
    if int(row["n"] if row["n"] is not None else 0) > 0:
        return 0
    added = 0
    for item in _LOTS:
        payload = {k: v for k, v in item.items() if k not in {"phone", "email", "website"}}
        lot_id, created = db.upsert_lot(conn, payload)
        if created:
            added += 1
        if item.get("phone"):
            db.add_contact_if_new(
                conn,
                lot_id,
                value=item["phone"],
                type="phone",
                party="winner",
                source="seed",
                confidence=1.0,
                snippet="demo",
            )
        if item.get("email"):
            db.add_contact_if_new(
                conn,
                lot_id,
                value=item["email"],
                type="email",
                party="winner",
                source="seed",
                confidence=1.0,
                snippet="demo",
            )
        if item.get("website"):
            db.add_contact_if_new(
                conn,
                lot_id,
                value=item["website"],
                type="website",
                party="winner",
                source="seed",
                confidence=1.0,
                snippet="demo",
            )
    return added
