"""Results from several Gmail accounts are ordered by when they were sent; an unreadable date sorts last."""

import pytest

from dreamference.chat.gmail_search_service import GmailSearchService


@pytest.mark.parametrize("message", [{}, {"date": ""}, {"date": "not a date"}, {"date": None}])
def test_a_missing_or_unreadable_date_sorts_last(message):
    assert GmailSearchService._sent_at(message) == 0.0


def test_a_date_with_an_unknown_zone_is_read_as_utc():
    unknown = GmailSearchService._sent_at({"date": "Fri, 30 Oct 2026 08:00:00 -0000"})
    utc = GmailSearchService._sent_at({"date": "Fri, 30 Oct 2026 08:00:00 +0000"})
    assert unknown == utc == 1793347200.0
