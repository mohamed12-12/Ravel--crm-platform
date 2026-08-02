from __future__ import annotations

import re
from datetime import date


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
