"""Порог суммы, регион, ключи — из config.yaml."""

from __future__ import annotations

import json
from typing import Any

import yaml

from app import config, db
from app.regions import collect_window, geo_words_from, regions_from


def load_filters() -> dict[str, Any]:
    raw = yaml.safe_load(config.FILTERS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"config.yaml пустой или сломан: {config.FILTERS_PATH}")
    return raw


def public_filters() -> dict[str, Any]:
    data = load_filters()
    keywords = data.get("keywords") or []
    exclude = data.get("exclude_keywords") or []
    okpd = data.get("okpd_prefixes") or []
    eis = data.get("eis") or {}
    collect = data.get("collect") or {}
    cutoff_iso_val, cutoff_ru_val = collect_window(collect)
    payload = {
        "region_code": str(data.get("region_code") or "77"),
        "amount_min": int(data.get("amount_min") or 500000),
        "own_inns": list(data.get("own_inns") or []),
        "okpd_prefixes": okpd,
        "keywords": keywords,
        "harvest_keywords": list(data.get("harvest_keywords") or keywords),
        "exclude_keywords": exclude,
        "weak_tokens": list(data.get("weak_tokens") or []),
        "keyword_groups": data.get("keyword_groups") or {},
        "eis": eis,
        "regions": regions_from(data),
        "geo_words": geo_words_from(data),
        "collect": collect,
        "collect_months": int(collect.get("months") or 12),
        "date_from_default": cutoff_iso_val,
        "date_from_ru": cutoff_ru_val,
        "keyword_count": len(keywords),
        "exclude_count": len(exclude),
    }
    return payload


def effective_filters(conn=None) -> dict[str, Any]:
    data = public_filters()
    own = conn is None
    dbc = db.connect() if own else conn
    assert dbc is not None
    try:
        saved = db.get_setting(dbc, "amount_min")
        if saved:
            data["amount_min"] = int(saved)
        disabled = db.get_setting(dbc, "disabled_profiles")
        if disabled:
            try:
                data["disabled_profiles"] = json.loads(disabled)
            except json.JSONDecodeError:
                data["disabled_profiles"] = []
        else:
            data["disabled_profiles"] = []
    finally:
        if own:
            dbc.close()
    return data


def ui_hints() -> dict[str, Any]:
    path = config.ROOT / "ui_hints.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}
