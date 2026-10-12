from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    """Small Telegram-friendly page object for bounded inline lists."""

    items: tuple[T, ...]
    page: int
    per_page: int
    total_items: int
    total_pages: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def indicator(self) -> str:
        return f"{self.page}/{self.total_pages}"


def paginate_sequence(items: Sequence[T], *, page: int = 1, per_page: int = 5) -> Page[T]:
    """Paginate an in-memory list for bot keyboards/messages with clamped pages."""

    if per_page <= 0:
        raise ValueError("per_page must be positive.")

    total_items = len(items)
    total_pages = max(1, ceil(total_items / per_page))
    current_page = min(max(page, 1), total_pages)
    start = (current_page - 1) * per_page
    end = start + per_page
    return Page(
        items=tuple(items[start:end]),
        page=current_page,
        per_page=per_page,
        total_items=total_items,
        total_pages=total_pages,
    )
