"""Applications Project Support email: an Outlook draft from the APE template, filled from the project.

Recipients come from the APE tracker sheet (PM, ME, Sales Manager) and SharePoint "Project Territory Teams.xlsx"
(ACE, Apps Team Lead, Apps Manager, always-CC), matched on the Sales Manager. The draft is only displayed, never sent.
"""
from __future__ import annotations

import difflib
import html
import json
import os
import re
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_REL = "Applications Project Support/APE Applications Project Support Template v3.oft"
TERRITORY_URL = (
    "https://hamiltonmarketing.sharepoint.com/sites/RNOAPE/_layouts/15/download.aspx"
    "?UniqueId=22C9E186-F1B0-40F2-92AF-1A4CA142F216"
)
TERRITORY_MAX_AGE = 7 * 24 * 3600
ROW_KEYS = {
    "pm": "pm",
    "sales": "sales",
    "ace": "ace",
    "ace manager": "ace_manager",
    "apps manager": "apps_manager",
    "apps team leads": "apps_lead",
    "apps team lead": "apps_lead",
    "l4 sales and ace": "l4",
}
EMPTY = {"", "n/a", "na", "intern'tl", "tbd", "-"}
DEFAULT_MANAGER = "Mark Cunningham"


def conf(cfg: dict) -> dict:
    return cfg.get("support_email") or {}


def template_path(cfg: dict) -> Path:
    raw = str(conf(cfg).get("template") or "")
    if raw:
        return Path(raw)
    return Path(str(cfg.get("templates_root") or "R:/APE_AE/TEMPLATES")) / TEMPLATE_REL


# ---------------------------------------------------------------- names

def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\xa0", " ")).strip(" *")


def split_people(cell) -> list[str]:
    """'Grayson Kochi & Alex Wixtrom' -> two names; 'Samantha Scheid/Bulzomi' -> both surnames; N/A -> []."""
    text = _clean(cell)
    if text.lower() in EMPTY or text.lower().startswith("none"):
        return []
    if text.lower().startswith("cc "):
        text = text[3:]
    out: list[str] = []
    for part in re.split(r"\s*(?:&|,|\band\b)\s*", text):
        part = _clean(part)
        if not part or part.lower() in EMPTY:
            continue
        part = _clean(re.sub(r"\([^)]*\)", "", part))
        if "/" in part:
            left, right = [_clean(item) for item in part.split("/", 1)]
            if right and " " not in right and " " in left:
                out.append("%s|%s %s" % (left, left.split()[0], right))
                continue
            out.extend(item for item in (left, right) if item)
            continue
        if part:
            out.append(part)
    return out


def _norm(name: str) -> str:
    return re.sub(r"[^a-z ]", "", _clean(name).lower())


NICKNAMES = {
    "bill": "william", "will": "william", "benny": "bernard", "bob": "robert", "rob": "robert",
    "mike": "michael", "jim": "james", "joe": "joseph", "dan": "daniel", "tom": "thomas",
    "dave": "david", "steve": "steven", "nick": "nicholas", "tony": "anthony", "liz": "elizabeth", "kate": "katherine",
}


def _first_match(first_a: str, first_b: str) -> bool:
    if first_a.startswith(first_b) or first_b.startswith(first_a):
        return True
    return NICKNAMES.get(first_a, first_a) == NICKNAMES.get(first_b, first_b)


def same_name(a: str, b: str, fuzzy_last: bool = False) -> bool:
    """Same person allowing a short first name or nickname: 'Phil Darden' ~ 'Phillip Darden', 'Bill Banks' ~ 'William Banks'.
    fuzzy_last also allows a one-letter surname spelling difference ('Grey' ~ 'Gray')."""
    left, right = _norm(a).split(), _norm(b).split()
    if not left or not right:
        return False
    if left == right:
        return True
    if left[-1] != right[-1]:
        if not fuzzy_last or difflib.SequenceMatcher(None, left[-1], right[-1]).ratio() < 0.75:
            return False
    return _first_match(left[0], right[0])


def best_name(name: str, known: list[str]) -> str:
    """The spelling Outlook uses (from names seen on emails), else the name as given.
    'A|B' holds alternatives (old and new surname): the one seen on emails wins, else the last."""
    if "|" in name:
        options = name.split("|")
        for option in options:
            found = best_name(option, known)
            if found in known:
                return found
        return options[-1]
    if not known:
        return name
    for item in known:
        if _norm(item) == _norm(name):
            return item
    for fuzzy in (False, True):
        found = [item for item in known if same_name(name, item, fuzzy_last=fuzzy)]
        if len(found) == 1:
            return found[0]
    return name


def known_names(cache_path: Path) -> list[str]:
    """Display names seen on To/CC/From of cached emails (the Outlook spelling)."""
    try:
        data = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = list(items.values())
    seen: dict[str, str] = {}
    for item in items or []:
        for field in ("to", "cc", "sender"):
            for part in str(item.get(field) or "").split(";"):
                part = _clean(part)
                if part and "@" not in part and " " in part:
                    seen.setdefault(_norm(part), part)
    return list(seen.values())


# ---------------------------------------------------------------- territory teams

def parse_territory(rows: list[list]) -> list[dict]:
    """Rows of the 'APE Territory Teams' sheet (label in column A, one territory per column) -> one dict per column."""
    labelled: dict[str, list] = {}
    cc_row: list = []
    for row in rows:
        if not row:
            continue
        label = _clean(row[0]).lower()
        if label in ROW_KEYS:
            labelled[ROW_KEYS[label]] = list(row[1:])
        elif any(_clean(cell).lower().startswith("cc ") for cell in row[1:]):
            cc_row = list(row[1:])
    width = max((len(values) for values in labelled.values()), default=0)
    teams: list[dict] = []
    for index in range(width):
        team: dict = {}
        extra_cc: list[str] = []
        for key, values in labelled.items():
            text = _clean(values[index] if index < len(values) else "")
            match = re.search(r"\(?\s*\bCC\s+([^)]*)\)?", text)
            main, copied = (text[: match.start()], match.group(1)) if match and match.start() > 0 else (text, "")
            team[key] = split_people(main)
            extra_cc += split_people(copied)
        cell = cc_row[index] if index < len(cc_row) else ""
        team["cc"] = split_people(cell) + [name for name in extra_cc if name not in split_people(cell)]
        if any(team.get(key) for key in ("sales", "ace", "apps_manager", "apps_lead")):
            teams.append(team)
    return teams


def territory_for(teams: list[dict], sales_manager: str) -> dict:
    if not sales_manager:
        return {}
    for team in teams:
        if any(same_name(sales_manager, name) for name in team.get("sales") or []):
            return team
    return {}


def territory_cache(cfg: dict) -> Path:
    return Path(str(conf(cfg).get("territory_cache") or (ROOT / "output" / "territory_teams.json")))


def download_territory(url: str, target: Path) -> Path:
    """Open the workbook read-only in a hidden Excel (signed in as the user) and save a local copy."""
    import pythoncom
    import win32com.client

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(".territory-%d.xlsx" % os.getpid())
    pythoncom.CoInitialize()
    excel = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        workbook = excel.Workbooks.Open(url, UpdateLinks=0, ReadOnly=True, Notify=False, AddToMru=False)
        try:
            workbook.SaveCopyAs(str(temp))
        finally:
            workbook.Close(SaveChanges=False)
    finally:
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
    os.replace(temp, target)
    return target


def load_territory(cfg: dict, fresh: bool = False, downloader=download_territory) -> tuple[list[dict], str]:
    """(teams, problem). Uses the cached copy unless it is older than a week or fresh is set."""
    cache = territory_cache(cfg)
    cached: list[dict] = []
    try:
        cached = json.loads(cache.read_text(encoding="utf-8")).get("teams") or []
        age = time.time() - cache.stat().st_mtime
    except (OSError, ValueError, AttributeError):
        age = None
    if cached and not fresh and age is not None and age < TERRITORY_MAX_AGE:
        return cached, ""
    try:
        import openpyxl

        workbook_path = downloader(str(conf(cfg).get("territory_url") or TERRITORY_URL), cache.with_suffix(".xlsx"))
        book = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
        try:
            teams = parse_territory([list(row) for row in book.worksheets[0].iter_rows(values_only=True)])
        finally:
            book.close()
        if not teams:
            raise ValueError("no territory columns found")
        cache.write_text(json.dumps({"teams": teams}, indent=1), encoding="utf-8")
        return teams, ""
    except Exception as exc:
        problem = "Territory Teams could not be read (%s)" % exc
        return cached, problem + ("; using the last copy" if cached else "")


# ---------------------------------------------------------------- the draft

def link_name(url: str, fallback: str) -> str:
    query = parse_qs(urlparse(url).query)
    name = (query.get("file") or [""])[0]
    if not name:
        tail = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        name = tail if "." in tail else ""
    return unquote(name) or fallback


def previous_drs(project: dict) -> list[str]:
    """Previous design reviews; older snapshots kept a single dr_form."""
    previous = project.get("previous_dr")
    if isinstance(previous, str):
        previous = [previous]
    return [url for url in (previous or ([project.get("dr_form")] if project.get("dr_form") else [])) if url]


def documents(project: dict) -> tuple[list[dict], list[str]]:
    level = str(project.get("level") or (project.get("sheet") or {}).get("level") or "").upper()
    docs: list[dict] = []
    missing: list[str] = []
    cpr = project.get("cpr")
    if cpr:
        docs.append({"label": "CPR", "url": cpr, "name": link_name(cpr, "CPR")})
    else:
        missing.append("CPR link")
    current = project.get("design_review")
    previous = previous_drs(project)
    if current:
        docs.append({"label": "Design Review", "url": current, "name": link_name(current, "Design Review")})
    else:
        if previous:
            docs.append({"label": "Previous DR", "url": previous[0], "name": link_name(previous[0], "Previous DR")})
        missing.append("Current DR link")
    if level == "L3":
        missing.append("FAT link (L3): add by hand")
    if level == "L4":
        missing.append("SOW link (L4): add by hand")
    return docs, missing


def subject_line(project: dict) -> str:
    sheet = project.get("sheet") or {}
    customer = project.get("customer") or sheet.get("customer") or "CustomerName"
    sos = ", ".join(project.get("so") or []) or ", ".join(project.get("qte") or []) or "SO-XXXXXX"
    part = project.get("project_no") or sheet.get("sk") or "020XXX"
    description = sheet.get("description") or project.get("name") or "Description"
    return "Applications Project Support - %s | %s | %s | %s" % (customer, sos, part, description)


def build_draft(project: dict, teams: list[dict], today: date, manager: str = DEFAULT_MANAGER, known: list[str] | None = None) -> dict:
    """Everything the draft needs, without Outlook. 'missing' lists roles/links to fill by hand."""
    sheet = project.get("sheet") or {}
    known = known or []
    missing: list[str] = []
    sales_manager = _clean(re.sub(r"\([^)]*\)", "", sheet.get("sales_manager") or ""))
    team = territory_for(teams, sales_manager)
    if not sales_manager:
        missing.append("Sales Manager (not on the APE sheet)")
    elif not team:
        missing.append("territory for Sales Manager %s (not in Territory Teams)" % sales_manager)

    def people(key: str, role: str) -> list[str]:
        names = [best_name(name, known) for name in team.get(key) or []]
        if team and not names:
            missing.append(role)
        return names

    ace = people("ace", "ACE")
    lead = people("apps_lead", "Apps Team Lead")
    apps_manager = people("apps_manager", "Apps Manager")
    level = str(project.get("level") or sheet.get("level") or "").upper()
    l4 = people("l4", "L4 Sales Manager / ACE (Territory Teams)") if level == "L4" else []
    pm = sheet.get("pm_name") or ""
    me = sheet.get("me_name") or ""
    if not pm:
        missing.append("PM")
    if not me:
        missing.append("ME")
    to: list[str] = []
    for name in ace + lead + apps_manager + l4:
        if name not in to:
            to.append(name)
    cc: list[str] = []
    for name in [pm, me, manager, best_name(sales_manager, known) if sales_manager else ""] + [best_name(n, known) for n in team.get("cc") or []]:
        if name and name not in cc and name not in to:
            cc.append(name)
    docs, doc_missing = documents(project)
    project_page = project.get("sharepoint") or ""
    if not project_page:
        doc_missing.insert(0, "Project page link")
    customer = project.get("customer") or sheet.get("customer") or ""
    sos = ", ".join(project.get("so") or []) or ", ".join(project.get("qte") or [])
    mentions = {
        "@applicationsTeamLead": l4 + lead,
        "@applicaitonsManager": apps_manager,
        "@sales": [best_name(sales_manager, known)] if sales_manager else [],
        "@ACE": ace,
    }
    replacements = {
        "addFromAboveSubject": ", ".join(item for item in (customer, sos) if item) or "addFromAboveSubject",
        "addOneWeekFromCurrentDate": (today + timedelta(days=7)).strftime("%m/%d/%Y"),
    }
    return {
        "subject": subject_line(project),
        "to": to,
        "cc": cc,
        "replacements": replacements,
        "mentions": mentions,
        "documents": docs,
        "project_page": project_page,
        "missing": missing + doc_missing + ["Scheduled test date (from the design review)"],
    }


DOCS_ANCHOR_RE = re.compile(r"(<li[^>]*>)((?:<span[^>]*>)*)((?:(?!</li>).)*?Part Compliance Testing Forms:)", re.S)
PROJECT_PAGE_RE = re.compile(r"(>[^<]*?)\b(project page)\b", re.I)


def _anchor(url: str, text: str) -> str:
    return '<a href="%s">%s</a>' % (html.escape(url, quote=True), text)


def mention(name: str, smtp: str) -> str:
    """An Outlook @mention: a mailto link whose id starts with OWAAM. Without an address, plain '@Name'."""
    text = "@" + html.escape(name)
    if not smtp:
        return text
    return (
        '<a id="OWAAM%s" href="mailto:%s"><span style=\'mso-no-proof:yes;text-decoration:none;text-underline:none\'>%s</span></a>'
        % (uuid.uuid4().hex.upper(), html.escape(smtp, quote=True), text)
    )


def fill_html(body: str, draft: dict, addresses: dict | None = None) -> str:
    addresses = addresses or {}
    values = {key: html.escape(value) for key, value in draft["replacements"].items()}
    for placeholder, names in (draft.get("mentions") or {}).items():
        values[placeholder] = " ".join(mention(name, addresses.get(name, "")) for name in names) or placeholder
    if values:
        pattern = re.compile("|".join(re.escape(key) for key in sorted(values, key=len, reverse=True)))
        body = pattern.sub(lambda m: values[m.group(0)], body)
    if draft.get("project_page"):
        body = PROJECT_PAGE_RE.sub(lambda m: m.group(1) + _anchor(draft["project_page"], m.group(2)), body, count=1)
    links = [_anchor(doc["url"], html.escape(doc["name"])) for doc in draft["documents"]]
    if not links:
        return body

    def insert(match: re.Match) -> str:
        li, spans = match.group(1), match.group(2)
        closing = "</span>" * spans.count("<span")
        items = "".join("%s%s%s<o:p></o:p>%s</li>" % (li, spans, link, closing) for link in links)
        return items + match.group(0)

    body, count = DOCS_ANCHOR_RE.subn(insert, body, count=1)
    if not count:
        body = body.replace("Documents:", "Documents:<ul>%s</ul>" % "".join("<li>%s</li>" % link for link in links), 1)
    return body


def smtp_address(session, name: str) -> str:
    """The person's email from the Outlook address book, or '' when the name doesn't resolve."""
    try:
        recipient = session.CreateRecipient(name)
        if not recipient.Resolve():
            return ""
        entry = recipient.AddressEntry
        user = entry.GetExchangeUser()
        return str((user.PrimarySmtpAddress if user is not None else "") or (entry.Address if "@" in str(entry.Address) else ""))
    except Exception:
        return ""


def open_draft(cfg: dict, draft: dict) -> list[str]:
    """Create the draft from the .oft, add recipients, show it. Never sends. Returns names Outlook could not resolve."""
    import pythoncom
    import win32com.client

    path = template_path(cfg)
    if not path.is_file():
        raise FileNotFoundError("Template not found: %s" % path)
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItemFromTemplate(str(path))
        mail.Subject = draft["subject"]
        names = {name for group in (draft.get("mentions") or {}).values() for name in group}
        mail.HTMLBody = fill_html(mail.HTMLBody, draft, {name: smtp_address(outlook.Session, name) for name in names})
        unresolved: list[str] = []
        for names, kind in ((draft["to"], 1), (draft["cc"], 2)):
            for name in names:
                recipient = mail.Recipients.Add(name)
                recipient.Type = kind
                if not recipient.Resolve():
                    unresolved.append(name)
        mail.Display(False)
        return unresolved
    finally:
        pythoncom.CoUninitialize()
