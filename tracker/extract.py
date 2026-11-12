from __future__ import annotations

import re
from datetime import date
from typing import Optional

ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\ufeff\u00a0]")
LINK_RE = re.compile(r"<((?:https?://)[^>\s]+)>")
DATE_TOKEN_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")

SHAREPOINT_RE = re.compile(r"Sharepoint\s+Link\s*<([^>]+)>", re.I)
ASSIGNED_RE = re.compile(r"assigned\s+to\s+([^.\n]+)", re.I)
GREETING_RE = re.compile(r"^\s*([A-Z][A-Za-z'\-]+)\s*,")
CHECK_HEADER_RE = re.compile(r"^\s*AE\s+Order\s+Review\s+Checklist\s*:?\s*$", re.I)
CHECK_STOP_RE = re.compile(r"^\s*ME\s+Order\s+Review\s+Checklist", re.I)
CHECK_ITEM_RE = re.compile(r"^\s*(?:\u2610|\[\s*\])\s*(.+)$")

PROJECT_PAGE_LABEL = r"Project\s+page"
CPR_LABEL = r"CPR(?:\s+form)?"
RMT_LABEL = r"Risk(?:s)?\s*(?:&|and)\s*Issues\s+document"
LEVEL_RE = re.compile(r"Project\s+level\s*:\s*(\S+)", re.I)
SHIP_DATE_RE = re.compile(r"Ship\s+date\s*:\s*(.+)", re.I)
DESIGN_NEED_BY_RE = re.compile(r"Design\s+need-by\s+date\s*:\s*(.+)", re.I)
CUSTOM_HEADER_RE = re.compile(r"^\s*Custom\s+parts\s*:?\s*$", re.I)
CUSTOM_STOP_RE = re.compile(r"^\s*(?:Project\s+timeline|Task\s+Name)\s*:?\s*$", re.I)
CUSTOM_SKIP_RE = re.compile(r"^(?:Part\s+Name|SK\s+Part\s*#?)$", re.I)
NUMBERING_RE = re.compile(r"^\d+\.\s*")

TIMELINE_RE = re.compile(r"^\s*Project\s+timeline\s*:?\s*$", re.I)
DURATION_RE = re.compile(r"^\d+(?:\.\d+)?\s+days?\??$")
TASK_NAME_RE = re.compile(r"^\s*Task\s+Name\s*$", re.I)
SUMMARY_ID_RE = re.compile(r"(?:SO|QTE)-?\d{5,6}|Kick", re.I)
SCHEDULE_DATE_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})/(\d{1,2})/(\d{2,4})$")
SO_PREFIX_RE = re.compile(r"^.+?\s-\sSO-\d{6}(?:,\s*SO-\d{6})*\s+")


def clean(text: str) -> str:
    return ZERO_WIDTH_RE.sub("", text or "").replace("\r\n", "\n").replace("\r", "\n")


def parse_us_date(text: str) -> Optional[date]:
    match = DATE_TOKEN_RE.search(text or "")
    if not match:
        return None
    month, day, year = (int(group) for group in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _label_url(lines: list[str], label: str) -> str:
    regex = re.compile(label, re.I)
    for line in lines:
        match = regex.search(line)
        if not match:
            continue
        link = LINK_RE.search(line, match.end())
        if link:
            return link.group(1).strip()
    return ""


def _split_names(text: str) -> list[str]:
    names = [part.strip(" .") for part in re.split(r"\s*,\s*|\s+and\s+", text or "") if part.strip(" .")]
    return [name for name in names if name]


def _checklist(lines: list[str]) -> list[str]:
    items: list[str] = []
    started = False
    blanks = 0
    for line in lines:
        if not started:
            if CHECK_HEADER_RE.match(line):
                started = True
            continue
        if CHECK_STOP_RE.match(line):
            break
        if not line.strip():
            blanks += 1
            if blanks >= 2:
                break
            continue
        blanks = 0
        match = CHECK_ITEM_RE.match(line)
        if match:
            items.append(re.sub(r"\s+", " ", match.group(1)).strip())
    return items


def _custom_parts(lines: list[str]) -> list[str]:
    parts: list[str] = []
    started = False
    blanks = 0
    for line in lines:
        if not started:
            if CUSTOM_HEADER_RE.match(line):
                started = True
            continue
        if CUSTOM_STOP_RE.match(line):
            break
        if not line.strip():
            blanks += 1
            if blanks >= 2:
                break
            continue
        blanks = 0
        text = NUMBERING_RE.sub("", line.strip()).strip()
        if not text or CUSTOM_SKIP_RE.match(text) or not re.search(r"[A-Za-z0-9]", text):
            continue
        parts.append(re.sub(r"\s+", " ", text))
    return parts


def parse_assignment_body(body: str) -> dict:
    text = clean(body)
    lines = text.split("\n")
    sharepoint = ""
    match = SHAREPOINT_RE.search(text)
    if match:
        sharepoint = match.group(1).strip()
    else:
        sharepoint = _label_url(lines, r"Sharepoint\s+Link")
    team: list[str] = []
    match = ASSIGNED_RE.search(text)
    if match:
        team = _split_names(match.group(1))
    pm_first_name = ""
    for line in lines:
        if not line.strip():
            continue
        greeting = GREETING_RE.match(line)
        if greeting:
            pm_first_name = greeting.group(1)
        break
    return {
        "sharepoint": sharepoint,
        "team": team,
        "pm_first_name": pm_first_name,
        "checklist": _checklist(lines),
        "rmt_note": "RMT" in text,
    }


def _schedule_date(text: str) -> Optional[date]:
    match = SCHEDULE_DATE_RE.match((text or "").strip())
    if not match:
        return None
    month, day, year = int(match.group(2)), int(match.group(3)), int(match.group(4))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_schedule(body: str, me_name: str = "") -> list[dict]:
    lines = clean(body).split("\n")
    start = None
    for index, line in enumerate(lines):
        if TIMELINE_RE.match(line):
            start = index + 1
            break
        if TASK_NAME_RE.match(line):
            following = [item.strip() for item in lines[index + 1:index + 4] if item.strip()]
            if following and following[0].lower() == "duration":
                start = index
                break
    if start is None:
        return []
    rows = [(index, line.strip()) for index, line in enumerate(lines[start:], start) if line.strip()]
    has_resources = any(line.lower().startswith("resource name") for _index, line in rows[:10])
    result: list[dict] = []
    position = 0
    while position < len(rows):
        name = rows[position][1]
        if position + 1 >= len(rows) or not DURATION_RE.match(rows[position + 1][1]):
            position += 1
            continue
        cursor = position + 2
        if cursor < len(rows) and _schedule_date(rows[cursor][1]) is None:
            cursor += 1
        if cursor + 1 >= len(rows):
            break
        start_date = _schedule_date(rows[cursor][1])
        finish_date = _schedule_date(rows[cursor + 1][1])
        if start_date is None or finish_date is None:
            position += 1
            continue
        cursor += 2
        resources = ""
        if has_resources and cursor < len(rows):
            candidate = rows[cursor][1]
            next_is_duration = cursor + 1 < len(rows) and bool(DURATION_RE.match(rows[cursor + 1][1]))
            if _schedule_date(candidate) is None and not next_is_duration:
                resources = candidate
                cursor += 1
        result.append(
            {
                "name": name,
                "start": start_date.isoformat(),
                "finish": finish_date.isoformat(),
                "resources": resources,
                "mine": bool(me_name) and me_name.lower() in resources.lower(),
            }
        )
        position = cursor
    if len(result) > 1 and not SUMMARY_ID_RE.search(result[0]["name"]):
        result = result[1:]
    return result


def parse_kickoff_body(body: str) -> dict:
    text = clean(body)
    lines = text.split("\n")
    level = ""
    match = LEVEL_RE.search(text)
    if match:
        value = match.group(1).strip().upper()
        level = value if re.fullmatch(r"L\d", value) else ("L" + value if value.isdigit() else value)
    ship_date_text = ""
    match = SHIP_DATE_RE.search(text)
    if match:
        ship_date_text = match.group(1).strip()
    design_text = ""
    match = DESIGN_NEED_BY_RE.search(text)
    if match:
        design_text = match.group(1).strip()
    ship_date = parse_us_date(ship_date_text)
    design_need_by = parse_us_date(design_text)
    return {
        "project_page": _label_url(lines, PROJECT_PAGE_LABEL),
        "cpr": _label_url(lines, CPR_LABEL),
        "rmt": _label_url(lines, RMT_LABEL),
        "level": level,
        "ship_date": ship_date.isoformat() if ship_date else None,
        "ship_date_text": ship_date_text,
        "design_need_by": design_need_by.isoformat() if design_need_by else None,
        "design_need_by_text": design_text,
        "custom_parts": _custom_parts(lines),
        "schedule": parse_schedule(text),
    }


def _link_title(prefix: str) -> str:
    text = ZERO_WIDTH_RE.sub(" ", prefix or "")
    words = text.split()[-8:]
    while words and words[-1].lower() in ("here", ":"):
        words.pop()
    title = " ".join(words).strip(" :-")
    return title or "link"


def extract_links(body: str) -> list[dict]:
    links: dict[str, dict] = {}
    for line in clean(body).split("\n"):
        for match in LINK_RE.finditer(line):
            url = match.group(1).strip()
            if url in links:
                continue
            links[url] = {"title": _link_title(line[: match.start()]), "url": url}
    return list(links.values())


def strip_project_prefix(name: str) -> str:
    stripped = SO_PREFIX_RE.sub("", name or "").strip()
    return stripped.lstrip(" -").strip() or (name or "").strip()
