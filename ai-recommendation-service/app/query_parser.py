"""Query understanding without an LLM: place names, prices, dates and durations in English and German become filters.

Deterministic on purpose, and the response returns what was understood, so behaviour can be explained and tested.
Dates are relative to Africa/Windhoek: "next weekend" means Windhoek's next weekend wherever the server runs. An
extracted phrase is removed from the text used for ranking, since "under N$1500" says nothing about what a tour is
like.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

WINDHOEK = ZoneInfo("Africa/Windhoek")
DEFAULT_RADIUS_KM = 100.0

MONTHS = {
    "january": 1, "januar": 1, "february": 2, "februar": 2, "march": 3, "märz": 3, "maerz": 3, "april": 4, "may": 5,
    "mai": 5, "june": 6, "juni": 6, "july": 7, "juli": 7, "august": 8, "september": 9, "october": 10, "oktober": 10,
    "november": 11, "december": 12, "dezember": 12,
}  # fmt: skip
_CURRENCY = r"(?:n\$|nad|nam\$)"
_AMOUNT = r"(\d{1,3}(?:[.,\s]\d{3})+|\d+)"
_PRICE_PATTERNS = (
    rf"\b(?:under|below|less than|max(?:imum)?|up to|at most|cheaper than|unter|bis zu|bis|höchstens|maximal|weniger als)\s*{_CURRENCY}?\s*{_AMOUNT}\s*(?:{_CURRENCY}|dollars?)?",  # noqa: E501
    rf"{_CURRENCY}\s*{_AMOUNT}\s*(?:or less|max|maximum|oder weniger)\b",
)  # fmt: skip


@dataclass
class ParsedQuery:
    text: str
    place: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    max_price: Decimal | None = None
    date_from: date | None = None
    date_to: date | None = None
    max_minutes: int | None = None
    min_minutes: int | None = None
    understood: dict[str, str] = field(default_factory=dict)


def normalise(value: str) -> str:
    """Lower case, without accents or punctuation: how place names are compared."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", value.replace("ß", "ss")) if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9 ]+", " ", stripped.lower()).strip()


def _amount(raw: str) -> Decimal:
    return Decimal(re.sub(r"[.,\s]", "", raw))


def _weekend_on_or_after(day: date) -> tuple[date, date]:
    if day.weekday() == 6:  # asked on a Sunday, this weekend is what's left of it: today
        return day, day
    saturday = day + timedelta(days=(5 - day.weekday()) % 7)
    return saturday, saturday + timedelta(days=1)


def _dates(text: str, today: date) -> tuple[date, date, str] | None:
    """(from, to, the words matched). Weekends are Saturday and Sunday, weeks start on Monday."""
    next_monday = today + timedelta(days=7 - today.weekday())
    rules = (
        (r"\b(?:next weekend|nächstes wochenende|naechstes wochenende|kommendes wochenende)\b", lambda: _weekend_on_or_after(next_monday)),  # noqa: E501
        (r"\b(?:this weekend|dieses wochenende|am wochenende)\b", lambda: _weekend_on_or_after(today)),
        (r"\b(?:tomorrow|morgen)\b", lambda: (today + timedelta(days=1), today + timedelta(days=1))),
        (r"\b(?:today|heute)\b", lambda: (today, today)),
        (r"\b(?:next week|nächste woche|naechste woche|kommende woche)\b", lambda: (next_monday, next_monday + timedelta(days=6))),  # noqa: E501
        (r"\b(?:this week|diese woche)\b", lambda: (today, today + timedelta(days=6 - today.weekday()))),
    )  # fmt: skip
    for pattern, resolve in rules:
        match = re.search(pattern, text)
        if match:
            start, end = resolve()
            return start, end, match.group(0)
    month_names = "|".join(MONTHS)
    match = re.search(rf"\b(?:in|im|during|for|next)\s+({month_names})\b", text)
    if match:
        month = MONTHS[match.group(1)]
        # The next time that month comes round. The current month counts, from today on.
        year = today.year + 1 if month < today.month else today.year
        start = max(date(year, month, 1), today)
        end = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
        return start, end, match.group(0)
    return None


def _duration(text: str) -> tuple[int | None, int | None, str] | None:
    """(min minutes, max minutes, the words matched)."""
    rules = (
        (r"\b(?:half[- ]day|halbtag(?:estour|es)?|halbtägig\w*|halbtaegig\w*)\b", None, 300),
        (r"\b(?:full[- ]day|all day|ganztag(?:estour|es)?|ganztägig\w*|ganztaegig\w*)\b", 360, 720),
        (r"\b(?:multi[- ]day|mehrtägig\w*|mehrtaegig\w*|overnight)\b", 1440, None),
        (r"\b(?:quick|short|kurz\w*)\b", None, 180),
    )
    for pattern, low, high in rules:
        match = re.search(pattern, text)
        if match:
            return low, high, match.group(0)
    match = re.search(
        r"\b(?:under|within|max(?:imum)?|unter|maximal|höchstens)\s+(\d{1,2})\s*(?:hours?|hrs?|stunden?|std)\b", text
    )
    if match:
        return None, int(match.group(1)) * 60, match.group(0)
    match = re.search(r"\b(\d{1,2})\s*[- ]?(?:hours?|hrs?|stunden?|std|stündig\w*)\b", text)
    if match:
        hours = int(match.group(1))
        return None, hours * 60, match.group(0)
    match = re.search(r"\b(\d{1,2})\s*[- ]?(?:days?|tage?|tägig\w*)\b", text)
    if match:
        days = int(match.group(1))
        return days * 1440 - 720 if days > 1 else 360, days * 1440, match.group(0)
    return None


class QueryParser:
    def __init__(self, gazetteer: dict[str, tuple[str, float, float]]) -> None:
        """gazetteer: normalised name -> (display name, latitude, longitude)."""
        self._places = gazetteer
        self._longest = max((len(name.split()) for name in gazetteer), default=1)

    def _place(self, text: str) -> tuple[str, tuple[str, float, float]] | None:
        """The longest place name in the text, so that "Walvis Bay" beats a "Bay" on its own."""
        words = normalise(text).split()
        for size in range(min(self._longest, len(words)), 0, -1):
            for start in range(len(words) - size + 1):
                candidate = " ".join(words[start : start + size])
                if candidate in self._places:
                    return candidate, self._places[candidate]
        return None

    def parse(self, query: str, now: datetime | None = None) -> ParsedQuery:
        today = (now or datetime.now(WINDHOEK)).astimezone(WINDHOEK).date()
        lowered = query.lower()
        parsed = ParsedQuery(text=query)
        remove: list[str] = []

        for pattern in _PRICE_PATTERNS:
            match = re.search(pattern, lowered)
            # A duration's number isn't a price: "under 3 hours" stays with _duration.
            if match and not re.match(r"\s*(?:hours?|hrs?|stunden?|std|days?|tage?)\b", lowered[match.end() :]):
                parsed.max_price = _amount(match.group(1))
                parsed.understood["maxPrice"] = match.group(0)
                remove.append(match.group(0))
                break

        dates = _dates(lowered, today)
        if dates:
            parsed.date_from, parsed.date_to = dates[0], dates[1]
            parsed.understood["dates"] = dates[2]
            remove.append(dates[2])

        duration = _duration(lowered)
        if duration:
            parsed.min_minutes, parsed.max_minutes = duration[0], duration[1]
            parsed.understood["duration"] = duration[2]
            remove.append(duration[2])

        place = self._place(lowered)
        if place:
            name, (display, latitude, longitude) = place
            parsed.place = display
            parsed.latitude, parsed.longitude, parsed.radius_km = latitude, longitude, DEFAULT_RADIUS_KM
            parsed.understood["place"] = display
            # The place stays in the ranking text: it often appears in descriptions, and it helps there.

        remaining = lowered
        for phrase in remove:
            remaining = remaining.replace(phrase, " ")
        parsed.text = re.sub(r"\s+", " ", remaining).strip(" ,.;:-") or query
        return parsed
