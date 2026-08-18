"""Сменный интерфейс коллектора: HTTP сейчас, Playwright позже без смены пайплайна."""

from __future__ import annotations

from typing import Protocol

from app.models import AttachmentDraft, ListingHit, LotDraft


class Collector(Protocol):
    name: str

    def fetch_listing(self, query: str, page: int) -> list[ListingHit]:
        ...

    def fetch_card(self, hit: ListingHit) -> LotDraft | None:
        ...

    def fetch_attachments(self, hit: ListingHit) -> list[AttachmentDraft]:
        ...
