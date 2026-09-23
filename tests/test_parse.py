# Synthetic JSON shapes. These are guesses at what StubHub might send, used only to test the
# parser's logic. They are not real StubHub data.

import pytest

from ticket.stubhub.parse import ListingParseError, extract, find_listing_arrays, map_listing, parse_money

EVENT = "https://www.stubhub.com/x/event/1"


def test_parse_money_variants():
    assert parse_money("S$1,731", None) == (1731.0, "SGD")
    assert parse_money("US$ 950.50", None) == (950.5, "USD")
    assert parse_money(1234, "SGD") == (1234.0, "SGD")
    assert parse_money({"amount": 99.5, "currency": "usd"}, None) == (99.5, "USD")
    assert parse_money({"formatted": "S$2,080"}, None) == (2080.0, "SGD")


def test_bare_dollar_is_not_guessed():
    assert parse_money("$1,000", None) == (1000.0, None)
    assert parse_money("$1,000", "SGD") == (1000.0, "SGD")


def test_parse_money_rejects_junk():
    for bad in ("call for price", True, {"x": 1}, "12abc"):
        with pytest.raises(ListingParseError):
            parse_money(bad, None)


def test_section_summaries_are_not_listings():
    doc = {"sections": [{"section": "16", "minPrice": "S$3,917"}, {"section": "8", "minPrice": "S$4,659"}]}
    doc_with_price = {"pins": [{"section": "16", "price": "S$3,917"}]}
    assert list(find_listing_arrays(doc)) == []
    assert list(find_listing_arrays(doc_with_price)) == []


def test_extract_maps_fields_and_dedupes():
    listings = [
        {"listingId": 11, "section": "Section 16", "row": "7", "availableTickets": 2,
         "splits": [2], "priceWithFees": "S$3,917", "rawPrice": 3300,
         "listingNotes": [{"formattedListingNoteContent": "Limited view"}]},
        {"listingId": 12, "sectionName": "Lower Level 15", "rowName": "A", "quantity": "4",
         "price": {"amount": 4222, "currency": "SGD"}},
    ]
    docs = [("https://www.stubhub.com/a", {"grid": {"items": listings}}),
            ("https://www.stubhub.com/b", {"items": listings[:1]})]
    res = extract(docs, 2, EVENT)
    assert [l.listing_id for l in res.listings] == ["11", "12"]
    assert res.duplicates == 1
    a, b = res.listings
    assert (a.section, a.row, a.quantity, a.allowed_splits) == ("16", "7", 2, [2])
    assert (a.price_per_ticket, a.currency, a.price_field, a.price_includes_fees) == (3917.0, "SGD", "priceWithFees", True)
    assert "priceWithFees" in a.price_candidates and "rawPrice" in a.price_candidates
    assert a.view_notes == ["Limited view"]
    assert (b.section, b.quantity, b.price_per_ticket, b.price_includes_fees) == ("15", 4, 4222.0, None)
    assert res.errors == []


def test_unknown_currency_is_reported_not_hidden():
    obj = {"id": 1, "section": "16", "row": "1", "price": "$900"}
    listing, issues = map_listing(obj, 2, EVENT)
    assert listing.currency is None
    assert any("currency unknown" in i for i in issues)


def test_bad_listing_is_an_error_not_dropped_silently():
    docs = [("u", {"items": [
        {"id": 1, "section": "16", "row": "1", "price": "S$100"},
        {"id": 2, "section": "16", "row": "1", "price": "ask me"},
    ]})]
    res = extract(docs, 2, EVENT)
    assert len(res.listings) == 1
    assert len(res.errors) == 1 and "ask me" in res.errors[0]


def test_missing_id_gets_content_hash_and_issue():
    listing, issues = map_listing({"section": "16", "row": "1", "price": "S$100"}, 2, EVENT)
    assert listing.listing_id.startswith("h:")
    assert any("content hash" in i for i in issues)
