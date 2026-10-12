import pytest

from apps.bot.pagination import paginate_sequence


def test_paginate_sequence_returns_bounded_page() -> None:
    page = paginate_sequence(tuple(range(12)), page=2, per_page=5)

    assert page.items == (5, 6, 7, 8, 9)
    assert page.page == 2
    assert page.total_items == 12
    assert page.total_pages == 3
    assert page.has_previous is True
    assert page.has_next is True
    assert page.indicator == "2/3"


def test_paginate_sequence_clamps_page_numbers() -> None:
    low_page = paginate_sequence(tuple(range(3)), page=-10, per_page=2)
    high_page = paginate_sequence(tuple(range(3)), page=10, per_page=2)

    assert low_page.items == (0, 1)
    assert low_page.page == 1
    assert high_page.items == (2,)
    assert high_page.page == 2


def test_paginate_sequence_supports_empty_lists() -> None:
    page = paginate_sequence((), page=3, per_page=5)

    assert page.items == ()
    assert page.page == 1
    assert page.total_items == 0
    assert page.total_pages == 1
    assert page.indicator == "1/1"


def test_paginate_sequence_rejects_invalid_page_size() -> None:
    with pytest.raises(ValueError):
        paginate_sequence((1, 2, 3), per_page=0)
