from __future__ import annotations

import re


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)(.*)$")


def format_agent_reply(text: str) -> str:
    """Return customer-facing plain text without markdown artifacts."""

    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return ""

    value = re.sub(r"\s+[-*•]\s+(?=\*{0,2}[A-Za-z\u0600-\u06ff])", "\n", value)
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
            list_number += 1
            line = f"{list_number}) {match.group(1).strip()}"
        else:
            if list_number and not re.match(r"^\d+[.)]\s+", line):
                list_number = 0
            line = re.sub(r"^(\d+)[.]\s+", r"\1) ", line)
        lines.append(line)

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()
