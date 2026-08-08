"""Rewrites internal-sounding wording ("CRM", "database", "backend") into
customer-safe phrasing and reformats list-shaped replies into prose --
style/wording cleanup, not the leak/safety detection response_guard.py
does.
"""
from __future__ import annotations

import re


_LIST_ITEM_RE = re.compile(r"^\s*(?P<marker>[-*\u2022]|\d+[.)])\s+(?P<text>.*)$")
_TECHNICAL_REPLACEMENTS = (
    (re.compile(r"\bverified\s+CRM\s+", re.IGNORECASE), ""),
    (re.compile(r"\bCRM\s+profile\b", re.IGNORECASE), "traveler profile"),
    (re.compile(r"\bCRM\s+details\b", re.IGNORECASE), "trip details"),
    (re.compile(r"\bCRM\s+images\b", re.IGNORECASE), "official images"),
    (re.compile(r"\bCRM\s+trip\b", re.IGNORECASE), "trip"),
    (re.compile(r"\bCRM\s+trips\b", re.IGNORECASE), "trips"),
    (re.compile(r"\bCRM\b", re.IGNORECASE), "our records"),
    (re.compile(r"\bdatabase\b", re.IGNORECASE), "our records"),
    (re.compile(r"\bbackend\b", re.IGNORECASE), "our system"),
    (re.compile(r"\btool calls?\b", re.IGNORECASE), "checks"),
    (re.compile(r"\binternal tools?\b", re.IGNORECASE), "checks"),
    (re.compile(r"\binternal search\b", re.IGNORECASE), "search"),
    (re.compile(r"\bsystem records?\b", re.IGNORECASE), "records"),
    (re.compile(r"\bAPI responses?\b", re.IGNORECASE), "responses"),
)
_ARABIC_TECHNICAL_REPLACEMENTS = (
    (re.compile(r"\bverified\s+CRM\s+", re.IGNORECASE), ""),
    (re.compile(r"\bCRM\b", re.IGNORECASE), "سجلاتنا"),
    (re.compile(r"\bdatabase\b", re.IGNORECASE), "سجلاتنا"),
    (re.compile(r"\bbackend\b", re.IGNORECASE), "نظامنا"),
    (re.compile(r"\btool calls?\b", re.IGNORECASE), "المراجعة"),
    (re.compile(r"\binternal tools?\b", re.IGNORECASE), "المراجعة"),
    (re.compile(r"\bAPI responses?\b", re.IGNORECASE), "الردود"),
)
_ARABIC_LETTER_RE = re.compile(r"[؀-ۿ]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")

_SUSPICIOUS_ENDINGS = (
    "i will",
    "i can",
    "i'll",
    "by the",
    "to the",
    "because",
    "and",
    "or",
    "the ravel",
    "ravel traveler",
    "ravel team to",
    "team to",
    "sent this request to the",
    "send this request to the",
)
_SUSPICIOUS_ARABIC_ENDINGS = (
    "\u0623\u0648",
    "\u0627\u0648",
    "\u0648",
    "\u0625\u0644\u0649",
    "\u0627\u0644\u0649",
    "\u0639\u0634\u0627\u0646",
    "\u0639\u0644\u0634\u0627\u0646",
    "\u0644\u0623\u0646",
    "\u0644\u0627\u0646",
    "\u0645\u0646 \u0623\u062c\u0644",
    "\u0645\u0646 \u0627\u062c\u0644",
    "\u0628\u062e\u0635\u0648\u0635",
    "\u0639\u0646",
)
_RAW_ERROR_PATTERNS = (
    re.compile(r"\b(?:traceback|runtimeerror|valueerror|exception|stack trace)\b", re.IGNORECASE),
    re.compile(r"\b(?:assistant_message|workflow_policy|required_step|tool_result|selected_trip_id)\b", re.IGNORECASE),
    re.compile(r"^\s*[\[{].*[\]}]\s*$", re.S),
)


def sanitize_traveler_reply(text: str) -> str:
    """Remove customer-visible implementation terms and exact room inventory."""

    value = str(text or "")
    # Substituting the English wording into an Arabic sentence produced replies
    # like "لم أجد رحلة ... في our records", so pick the table matching the
    # dominant script instead of the language of the term being replaced.
    arabic_dominant = len(_ARABIC_LETTER_RE.findall(value)) > len(_LATIN_LETTER_RE.findall(value))
    for pattern, replacement in (_ARABIC_TECHNICAL_REPLACEMENTS if arabic_dominant else _TECHNICAL_REPLACEMENTS):
        value = pattern.sub(replacement, value)
    # Real room/seat counts are validated by response_guard's context-aware
    # inventory check (allow_inventory_counts), not stripped unconditionally
    # here — this used to blank out "2 rooms available" to just "available"
    # before the guard could ever see or allow the grounded count through.
    value = re.sub(r"\bPassport verified\b", "Passport uploaded and pending review", value, flags=re.IGNORECASE)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def format_agent_reply(text: str) -> str:
    """Return customer-facing plain text without markdown artifacts."""

    value = sanitize_traveler_reply(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return ""

    value = re.sub(r"(?<=[A-Za-z\u0600-\u06ff])\s+-\s+(?=[A-Za-z\u0600-\u06ff])", " __LABEL_DASH__ ", value)

    value = re.sub(r"\s+[-*\u2022]\s+(?=\*{0,2}[A-Za-z\u0600-\u06ff])", "\n", value)
    value = value.replace(" __LABEL_DASH__ ", " - ")
    value = value.replace("**", "").replace("__", "").replace("`", "")
    value = value.replace("*", "")

    lines: list[str] = []
    list_number = 0
    for raw_line in value.split("\n"):
        line = re.sub(r"[ \t]{2,}", " ", raw_line).strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        match = _LIST_ITEM_RE.match(line)
        if match:
            marker = match.group("marker")
            item_text = match.group("text").strip()
            if marker[0].isdigit():
                number = re.match(r"\d+", marker).group(0)
                line = f"{number}) {item_text}"
            else:
                list_number += 1
                line = f"{list_number}) {item_text}"
        else:
            if list_number and not re.match(r"^\d+[.)]\s+", line):
                list_number = 0
            line = re.sub(r"^(\d+)[.]\s+", r"\1) ", line)
        lines.append(line)

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


def response_completeness_issue(text: str) -> str:
    """Return a short internal reason when customer text is not final-safe."""

    value = str(text or "").strip()
    if not value:
        return "empty_response"
    for pattern in _RAW_ERROR_PATTERNS:
        if pattern.search(value):
            return "internal_or_raw_content"
    compact = re.sub(r"\s+", " ", value).strip()
    lowered = compact.casefold()
    if lowered.endswith(_SUSPICIOUS_ENDINGS):
        return "suspicious_incomplete_ending"
    last_words = compact.rstrip(".!?\u061f\u060c,").split()
    if last_words:
        last_word = last_words[-1].strip()
        if last_word in _SUSPICIOUS_ARABIC_ENDINGS:
            return "suspicious_incomplete_ending"
    if len(compact) < 12:
        return "" if re.fullmatch(r"[\w\u0600-\u06ff\s.!\u061f?\u060c,]+", compact) else "too_short"
    if compact[-1] in {":", ",", "-", "(", "[", "{", "\"", "'"}:
        return "dangling_punctuation"
    if compact.count("(") > compact.count(")") or compact.count("[") > compact.count("]") or compact.count("{") > compact.count("}"):
        return "unbalanced_delimiter"
    if compact.count('"') % 2 == 1:
        return "unbalanced_quote"
    lines = [line.strip() for line in value.splitlines()]
    for line in lines:
        if re.match(r"^(?:[-*\u2022]|\d+[.)])\s*$", line):
            return "empty_list_item"
        match = _LIST_ITEM_RE.match(line)
        if match and not match.group("text").strip():
            return "empty_list_item"
    last_line = next((line for line in reversed(lines) if line), "")
    last_match = _LIST_ITEM_RE.match(last_line)
    if last_match and response_completeness_issue(last_match.group("text")):
        return "incomplete_list_item"
    return ""


def is_complete_traveler_reply(text: str) -> bool:
    return response_completeness_issue(text) == ""
