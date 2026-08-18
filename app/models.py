"""Черновики лота между коллектором и БД."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ListingHit:
    external_id: str
    source: str
    url: str
    subject: str = ""
    amount_rub: int | None = None
    customer_name: str = ""
    customer_inn: str | None = None
    signed_at: str | None = None
    published_at: str | None = None
    fz: str = "44"
    extra: dict = field(default_factory=dict)


@dataclass
class ContactDraft:
    value: str
    type: str
    party: str
    source: str
    confidence: float = 0.8
    snippet: str = ""


@dataclass
class AttachmentDraft:
    url: str
    filename: str = ""


@dataclass
class LotDraft:
    external_id: str
    source: str
    url: str
    subject: str = ""
    amount_rub: int | None = None
    region_code: str = ""
    region_text: str = ""
    published_at: str | None = None
    signed_at: str | None = None
    fz: str = "44"
    okpd_codes: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    customer_name: str = ""
    customer_inn: str | None = None
    winner_name: str = ""
    winner_inn: str | None = None
    winner_status: str = ""
    profiles: list[str] = field(default_factory=list)
    reason: str = ""
    contacts: list[ContactDraft] = field(default_factory=list)
    attachments: list[AttachmentDraft] = field(default_factory=list)
