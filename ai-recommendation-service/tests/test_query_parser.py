"""Query understanding in English and German, including where Windhoek's clock decides the date."""

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.query_parser import DEFAULT_RADIUS_KM, QueryParser, normalise

WINDHOEK = ZoneInfo("Africa/Windhoek")
# A Friday, in the middle of the Windhoek afternoon.
FRIDAY = datetime(2026, 11, 6, 15, 0, tzinfo=WINDHOEK)

GAZETTEER = {
    "walvis bay": ("Walvis Bay", -22.957, 14.508),
    "walfischbucht": ("Walfischbucht", -22.957, 14.508),
    "etosha": ("Etosha", -18.855, 16.329),
    "luderitz": ("Luderitz", -26.648, 15.159),
    "windhoek": ("Windhoek", -22.559, 17.083),
}


@pytest.fixture
def parser() -> QueryParser:
    return QueryParser(GAZETTEER)


@pytest.mark.parametrize(
    ("query", "price"),
    [
        ("kayak under N$1500", 1500),
        ("kayak under 1500 NAD", 1500),
        ("kayak below n$ 1,500", 1500),
        ("kayak max 2000", 2000),
        ("kayak up to N$900", 900),
        ("Kajak unter 1500 NAD", 1500),
        ("Kajak bis zu N$ 1.500", 1500),
        ("Kajak höchstens 800", 800),
        ("kayak N$1200 or less", 1200),
    ],
)
def test_price_phrases(parser, query, price):
    assert parser.parse(query, FRIDAY).max_price == Decimal(price)


def test_a_price_phrase_leaves_the_ranking_text(parser):
    parsed = parser.parse("sunset cruise under N$1500", FRIDAY)
    assert parsed.text == "sunset cruise"
    assert parsed.understood["maxPrice"] == "under n$1500"


def test_a_duration_is_not_a_price(parser):
    parsed = parser.parse("hike under 3 hours", FRIDAY)
    assert parsed.max_price is None
    assert parsed.max_minutes == 180


@pytest.mark.parametrize(
    ("query", "start", "end"),
    [
        ("next weekend", date(2026, 11, 14), date(2026, 11, 15)),
        ("nächstes Wochenende", date(2026, 11, 14), date(2026, 11, 15)),
        ("this weekend", date(2026, 11, 7), date(2026, 11, 8)),
        ("am Wochenende", date(2026, 11, 7), date(2026, 11, 8)),
        ("tomorrow", date(2026, 11, 7), date(2026, 11, 7)),
        ("morgen", date(2026, 11, 7), date(2026, 11, 7)),
        ("next week", date(2026, 11, 9), date(2026, 11, 15)),
        ("nächste Woche", date(2026, 11, 9), date(2026, 11, 15)),
        ("in December", date(2026, 12, 1), date(2026, 12, 31)),
        ("im Dezember", date(2026, 12, 1), date(2026, 12, 31)),
    ],
)
def test_dates(parser, query, start, end):
    parsed = parser.parse(f"game drive {query}", FRIDAY)
    assert (parsed.date_from, parsed.date_to) == (start, end)


def test_a_month_already_past_means_next_year(parser):
    parsed = parser.parse("dunes in March", FRIDAY)
    assert (parsed.date_from, parsed.date_to) == (date(2027, 3, 1), date(2027, 3, 31))


def test_the_current_month_starts_today(parser):
    assert parser.parse("dunes in November", FRIDAY).date_from == date(2026, 11, 6)


def test_tomorrow_follows_windhoeks_clock_not_utc(parser):
    # 22:30 UTC on the 6th is already 00:30 on the 7th in Windhoek (UTC+2): tomorrow is the 8th.
    late = datetime(2026, 11, 6, 22, 30, tzinfo=UTC)
    assert parser.parse("tomorrow", late).date_from == date(2026, 11, 8)


def test_next_weekend_from_a_saturday_is_not_this_one(parser):
    saturday = datetime(2026, 11, 7, 9, 0, tzinfo=WINDHOEK)
    assert parser.parse("next weekend", saturday).date_from == date(2026, 11, 14)
    assert parser.parse("this weekend", saturday).date_from == date(2026, 11, 7)


def test_this_weekend_on_a_sunday_is_today(parser):
    sunday = datetime(2026, 11, 8, 9, 0, tzinfo=WINDHOEK)
    parsed = parser.parse("this weekend", sunday)
    assert (parsed.date_from, parsed.date_to) == (date(2026, 11, 8), date(2026, 11, 8))


@pytest.mark.parametrize(
    ("query", "low", "high"),
    [
        ("half day hike", None, 300),
        ("Halbtagestour", None, 300),
        ("full day drive", 360, 720),
        ("ganztägig", 360, 720),
        ("multi-day trek", 1440, None),
        ("mehrtägige Tour", 1440, None),
        ("3 hour walk", None, 180),
        ("3-stündig", None, 180),
        ("2 day trek", 2160, 2880),
    ],
)
def test_durations(parser, query, low, high):
    parsed = parser.parse(query, FRIDAY)
    assert (parsed.min_minutes, parsed.max_minutes) == (low, high)


def test_a_place_becomes_a_geo_filter_and_stays_in_the_text(parser):
    parsed = parser.parse("kayaking near Walvis Bay", FRIDAY)
    assert parsed.place == "Walvis Bay"
    assert (parsed.latitude, parsed.longitude) == (-22.957, 14.508)
    assert parsed.radius_km == DEFAULT_RADIUS_KM
    assert "walvis bay" in parsed.text


def test_german_and_accented_place_names(parser):
    assert parser.parse("Paddeln bei Walfischbucht", FRIDAY).latitude == -22.957
    assert parser.parse("Jugendstil in Lüderitz", FRIDAY).place == "Luderitz"


def test_the_longest_name_wins():
    parser = QueryParser({"bay": ("Bay", 1.0, 1.0), "walvis bay": ("Walvis Bay", 2.0, 2.0)})
    assert parser.parse("walvis bay kayak", FRIDAY).place == "Walvis Bay"


def test_nothing_to_understand_changes_nothing(parser):
    parsed = parser.parse("sunset photography, no long hikes", FRIDAY)
    assert parsed.understood == {}
    assert parsed.max_price is None
    assert parsed.text == "sunset photography, no long hikes"


def test_a_query_of_only_a_filter_keeps_some_text(parser):
    assert parser.parse("under N$1500", FRIDAY).text == "under N$1500"


def test_everything_together(parser):
    parsed = parser.parse("easy game drive in Etosha next weekend under N$2000, half day", FRIDAY)
    assert parsed.place == "Etosha"
    assert parsed.max_price == Decimal(2000)
    assert parsed.date_from == date(2026, 11, 14)
    assert parsed.max_minutes == 300
    assert set(parsed.understood) == {"place", "maxPrice", "dates", "duration"}


def test_normalise():
    assert normalise("Lüderitz") == "luderitz"
    assert normalise("Ai-Ais") == "ai ais"
    assert normalise("Viktoriafälle") == "viktoriafalle"
    assert normalise("Straße") == "strasse"
