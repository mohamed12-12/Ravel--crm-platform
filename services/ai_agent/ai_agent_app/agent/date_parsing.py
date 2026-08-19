"""Parses customer-supplied birthdate/expiry-date text in whatever loose
format it actually arrives in (Arabic-Indic digits, month names, partial
dates), rather than requiring a strict format the customer would need to
be told about.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta


_DIGIT_TRANSLATION = str.maketrans(
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9",
    "01234567890123456789",
)

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def normalize_birthdate_input(value: str, *, today: date | None = None) -> str:
    text = str(value or "").strip().translate(_DIGIT_TRANSLATION)
    if not text:
        return ""
    today = today or date.today()
    text = re.sub(r"[,،]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    for year, month, day in _numeric_candidates(text, today=today):
        normalized = _valid_date(year, month, day, today=today)
        if normalized:
            return normalized

    for year, month, day in _month_name_candidates(text, today=today):
        normalized = _valid_date(year, month, day, today=today)
        if normalized:
            return normalized

    return ""


def _normalize_year(raw_year: str, *, today: date) -> int:
    year = int(raw_year)
    if len(raw_year) == 2:
        current_two_digits = today.year % 100
        return 2000 + year if year <= current_two_digits else 1900 + year
    return year


def _numeric_candidates(text: str, *, today: date) -> list[tuple[int, int, int]]:
    candidates: list[tuple[int, int, int]] = []
    pattern = re.compile(r"(?<!\d)(\d{1,4})[\s./-](\d{1,2})[\s./-](\d{1,4})(?!\d)")
    for match in pattern.finditer(text):
        first, second, third = match.groups()
        if len(first) == 4:
            candidates.append((int(first), int(second), int(third)))
            continue
        if len(third) not in {2, 4}:
            continue
        year = _normalize_year(third, today=today)
        first_num = int(first)
        second_num = int(second)
        if first_num > 12:
            candidates.append((year, second_num, first_num))
        elif second_num > 12:
            candidates.append((year, first_num, second_num))
        else:
            candidates.append((year, second_num, first_num))
    return candidates


def _month_name_candidates(text: str, *, today: date) -> list[tuple[int, int, int]]:
    month_names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    day_first = re.compile(
        rf"(?<!\w)(\d{{1,2}})\s+({month_names})\.?\s+(\d{{2,4}})(?!\w)",
        re.IGNORECASE,
    )
    month_first = re.compile(
        rf"(?<!\w)({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+(\d{{2,4}})(?!\w)",
        re.IGNORECASE,
    )
    year_first = re.compile(
        rf"(?<!\w)(\d{{2,4}})\s+({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?!\w)",
        re.IGNORECASE,
    )
    candidates: list[tuple[int, int, int]] = []
    for match in day_first.finditer(text):
        day, month, year = match.groups()
        candidates.append((_normalize_year(year, today=today), _MONTHS[month.lower()], int(day)))
    for match in month_first.finditer(text):
        month, day, year = match.groups()
        candidates.append((_normalize_year(year, today=today), _MONTHS[month.lower()], int(day)))
    for match in year_first.finditer(text):
        year, month, day = match.groups()
        candidates.append((_normalize_year(year, today=today), _MONTHS[month.lower()], int(day)))
    return candidates


def normalize_expiry_date_input(value: str) -> str:
    """Flexible date parse for an expiry date, which -- unlike a birthday -- may
    validly be in the past (that is exactly the case the caller needs to detect
    and reject), so this does not filter out future OR past dates itself."""
    text = str(value or "").strip().translate(_DIGIT_TRANSLATION)
    if not text:
        return ""
    today = date.today()
    text = re.sub(r"[,،]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    for year, month, day in _numeric_candidates(text, today=today):
        normalized = _valid_calendar_date(year, month, day)
        if normalized:
            return normalized

    for year, month, day in _month_name_candidates(text, today=today):
        normalized = _valid_calendar_date(year, month, day)
        if normalized:
            return normalized

    return ""


def _valid_calendar_date(year: int, month: int, day: int) -> str:
    if year < 1900 or year > 2200:
        return ""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def compute_age(birthday_iso: str, *, today: date | None = None) -> int | None:
    """Return the age in whole years for an ISO (YYYY-MM-DD) birthday, or None if unparseable."""
    text = str(birthday_iso or "").strip()
    if not text:
        return None
    try:
        year_str, month_str, day_str = text.split("-")
        born = date(int(year_str), int(month_str), int(day_str))
    except (ValueError, TypeError):
        return None
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _valid_date(year: int, month: int, day: int, *, today: date) -> str:
    if year < 1900:
        return ""
    try:
        candidate = date(year, month, day)
    except ValueError:
        return ""
    if candidate > today:
        return ""
    return candidate.isoformat()


_ARABIC_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3,
    "ابريل": 4, "أبريل": 4, "إبريل": 4,
    "مايو": 5, "يونيو": 6, "يونية": 6, "يوليو": 7, "يولية": 7,
    "اغسطس": 8, "أغسطس": 8, "سبتمبر": 9,
    "اكتوبر": 10, "أكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}

# Fixed-offset phrases only -- "بعد يومين" is the Arabic DUAL form of "day"
# (unambiguously exactly 2, unlike a plural), so this is a literal mapping,
# not a guess.
_RELATIVE_DAY_OFFSET_TERMS = {
    "بكرة": 1, "بكره": 1, "غدا": 1, "غداً": 1, "tomorrow": 1,
    "بعد بكرة": 2, "بعد بكره": 2, "بعد يومين": 2, "بعد يومين ": 2,
    "day after tomorrow": 2,
}

_END_OF_MONTH_TERMS = (
    "اخر الشهر", "آخر الشهر", "نهاية الشهر",
    "end of month", "end of the month", "later this month",
)

_END_OF_PERIOD_MARKERS = ("اخر", "آخر", "نهاية", "end of")

_NEXT_WEEK_TERMS = (
    "الاسبوع الجاي", "الأسبوع الجاي", "الاسبوع القادم", "الأسبوع القادم",
    "next week", "next weekend",
)


def _last_day_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _nearest_future_day_of_month(today: date, day: int) -> str:
    """"بعد يوم 25" names a day-of-month with no month stated -- resolve to
    the nearest FUTURE occurrence (this month if that day hasn't passed yet,
    otherwise next month), a documented deterministic rule rather than a
    guessed date."""
    last_day_this_month = calendar.monthrange(today.year, today.month)[1]
    if today.day <= day <= last_day_this_month:
        return date(today.year, today.month, day).isoformat()
    year, month = today.year, today.month + 1
    if month > 12:
        month, year = 1, year + 1
    clamped_day = min(day, calendar.monthrange(year, month)[1])
    return date(year, month, clamped_day).isoformat()


def normalize_relative_date_input(text: str, *, today: date | None = None) -> str:
    """Resolve an unambiguous relative-date phrase ("بكره", "بعد يومين",
    "آخر الشهر", "end of August", "next week") to an ISO date, anchored on
    `today` (the caller's business "now", not this module's clock). Returns
    "" for anything genuinely ambiguous (e.g. a bare day-of-month with no
    other anchor is resolved via the documented nearest-future rule below,
    never invented outright) or unrecognized -- callers must treat "" the
    same as no date given, never as a computed empty date.
    """
    raw = str(text or "").strip().translate(_DIGIT_TRANSLATION)
    if not raw:
        return ""
    today = today or date.today()
    lowered = re.sub(r"\s+", " ", raw.casefold()).strip()

    for phrase, offset in _RELATIVE_DAY_OFFSET_TERMS.items():
        if phrase.strip() in lowered:
            return (today + timedelta(days=offset)).isoformat()

    match = re.search(r"(?:بعد|in)\s+(\d{1,2})\s*(?:يوم|ايام|أيام|days?)\b", lowered)
    if match:
        return (today + timedelta(days=int(match.group(1)))).isoformat()

    match = re.search(r"(?:بعد يوم|after (?:the )?)\s*(\d{1,2})\b", lowered)
    if match:
        day = int(match.group(1))
        if 1 <= day <= 31:
            return _nearest_future_day_of_month(today, day)

    if any(term in lowered for term in _END_OF_MONTH_TERMS):
        return _last_day_of_month(today.year, today.month).isoformat()

    for name, month in {**_MONTHS, **_ARABIC_MONTHS}.items():
        if name in lowered and any(marker in lowered for marker in _END_OF_PERIOD_MARKERS):
            year = today.year if month >= today.month else today.year + 1
            return _last_day_of_month(year, month).isoformat()

    if any(term in lowered for term in _NEXT_WEEK_TERMS):
        return (today + timedelta(days=7)).isoformat()

    return ""
