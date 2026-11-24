from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from ..models import Email

MAIL_ITEM = 43
APPOINTMENT_ITEM = 26
FOLDER_INBOX = 6
FOLDER_SENT = 5
FOLDER_CALENDAR = 9
DEFAULT_FOLDERS = {"inbox": FOLDER_INBOX, "sent items": FOLDER_SENT, "sent": FOLDER_SENT}
# Deleted Items, Outbox, Drafts, Conflicts, Sync Issues, Local/Server Failures, Junk Email.
SKIPPED_DEFAULT_FOLDERS = (3, 4, 16, 19, 20, 21, 22, 23)
MAIL_FOLDER_TYPE = 0
SCOPE_MAILBOX = "mailbox"
SCOPE_FOLDERS = "folders"
PR_INTERNET_MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
PR_SMTP_ADDRESS = "http://schemas.microsoft.com/mapi/proptag/0x39FE001F"


def _local_tz():
    try:
        from tzlocal import get_localzone

        return get_localzone()
    except Exception:
        return datetime.now().astimezone().tzinfo


def _local(value) -> Optional[datetime]:
    """Outlook COM returns local wall-clock times tagged as UTC; re-tag them as local."""
    if value is None:
        return None
    naive = datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
    if naive.year >= 4000:
        return None
    return naive.replace(tzinfo=_local_tz())


def _restrict_date(value: datetime) -> str:
    return value.strftime("%m/%d/%Y %I:%M %p")


def _namespace():
    import win32com.client

    try:
        app = win32com.client.GetActiveObject("Outlook.Application")
    except Exception:
        app = win32com.client.Dispatch("Outlook.Application")
    return app.GetNamespace("MAPI")


def _folder(namespace, name: str):
    default = DEFAULT_FOLDERS.get(name.strip().lower())
    if default is not None:
        return namespace.GetDefaultFolder(default)
    inbox = namespace.GetDefaultFolder(FOLDER_INBOX)
    node = inbox.Parent
    for part in name.replace("\\", "/").split("/"):
        try:
            node = node.Folders[part]
        except Exception:
            try:
                node = inbox.Folders[part]
            except Exception:
                return None
    return node


def _smtp(item) -> str:
    try:
        if str(item.SenderEmailType).upper() == "EX":
            user = item.Sender.GetExchangeUser()
            if user is not None:
                return str(user.PrimarySmtpAddress or "")
            return str(item.Sender.PropertyAccessor.GetProperty(PR_SMTP_ADDRESS) or "")
    except Exception:
        pass
    return str(getattr(item, "SenderEmailAddress", "") or "")


def _message_id(item) -> str:
    try:
        value = item.PropertyAccessor.GetProperty(PR_INTERNET_MESSAGE_ID)
        if value:
            return str(value).strip().strip("<>")
    except Exception:
        pass
    return "outlook:" + str(getattr(item, "EntryID", "") or "")


def display_item(entry_id: str) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        namespace = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        namespace.GetItemFromID(str(entry_id)).Display()
    finally:
        pythoncom.CoUninitialize()


def _me(namespace) -> str:
    try:
        entry = namespace.CurrentUser.AddressEntry
        user = entry.GetExchangeUser()
        return str(user.PrimarySmtpAddress if user is not None else entry.Address)
    except Exception:
        return ""


def _mail(item, folder_name: str, sender_smtp: Optional[str] = None) -> Optional[Email]:
    if getattr(item, "Class", None) != MAIL_ITEM:
        return None
    when = _local(getattr(item, "ReceivedTime", None)) or _local(getattr(item, "SentOn", None))
    if when is None:
        return None
    return Email(
        message_id=_message_id(item),
        subject=str(getattr(item, "Subject", "") or ""),
        sender="%s <%s>" % (
            getattr(item, "SenderName", "") or "",
            sender_smtp if sender_smtp is not None else getattr(item, "SenderEmailAddress", "") or "",
        ),
        to=str(getattr(item, "To", "") or ""),
        cc=str(getattr(item, "CC", "") or ""),
        date=when,
        body_text=str(getattr(item, "Body", "") or ""),
        source_path="outlook:" + folder_name,
        entry_id=str(getattr(item, "EntryID", "") or ""),
    )


def scope(settings: dict) -> str:
    value = str(settings.get("scope") or SCOPE_MAILBOX).strip().lower()
    return value if value in (SCOPE_MAILBOX, SCOPE_FOLDERS) else SCOPE_MAILBOX


def _entry_id(folder) -> str:
    try:
        return str(folder.EntryID)
    except Exception:
        return ""


def _skipped(path: str, name: str, skip: list[str]) -> bool:
    path = path.lower()
    name = name.lower()
    for item in skip:
        item = item.replace("\\", "/").strip("/").lower()
        if item and (item == name or item == path or path.endswith("/" + item) or path.startswith(item + "/")):
            return True
    return False


def mailbox_folders(namespace, skip: Iterable[str] = ()) -> list[tuple[str, object, bool]]:
    """(path, folder, is_sent) for every mail folder in the default mailbox, minus junk/deleted/drafts/outbox
    and anything named in skip (a folder name like "Graymail" or a path like "Inbox/IT Announcements";
    skipping a folder skips its subfolders)."""
    inbox = namespace.GetDefaultFolder(FOLDER_INBOX)
    root = inbox.Parent
    excluded = set()
    for kind in SKIPPED_DEFAULT_FOLDERS:
        try:
            excluded.add(_entry_id(namespace.GetDefaultFolder(kind)))
        except Exception:
            continue
    excluded.discard("")
    try:
        sent_id = _entry_id(namespace.GetDefaultFolder(FOLDER_SENT))
    except Exception:
        sent_id = ""
    skip = [str(item) for item in skip or [] if str(item).strip()]
    found: list[tuple[str, object, bool]] = []

    def walk(folder, path: str) -> None:
        try:
            children = list(folder.Folders)
        except Exception:
            return
        for child in children:
            try:
                name = str(child.Name)
            except Exception:
                continue
            child_path = (path + "/" + name) if path else name
            if _entry_id(child) in excluded or _skipped(child_path, name, skip):
                continue
            try:
                is_mail = child.DefaultItemType == MAIL_FOLDER_TYPE
            except Exception:
                is_mail = False
            if not is_mail:
                continue
            found.append((child_path, child, bool(sent_id) and _entry_id(child) == sent_id))
            walk(child, child_path)

    walk(root, "")
    return found


def collect(config: dict, since_days: Optional[int] = None) -> list[Email]:
    settings = config.get("outlook_com") or {}
    if not settings.get("enabled"):
        return []
    days = int(since_days or settings.get("days_back") or 120)
    cutoff = datetime.now() - timedelta(days=days)
    namespace = _namespace()
    me = _me(namespace)
    emails: list[Email] = []
    if scope(settings) == SCOPE_MAILBOX:
        targets = mailbox_folders(namespace, settings.get("skip_folders") or [])
    else:
        targets = []
        for folder_name in settings.get("folders") or []:
            folder = _folder(namespace, folder_name)
            if folder is None:
                print("outlook: folder not found: %s" % folder_name)
                continue
            targets.append((folder_name, folder, folder_name.strip().lower().startswith("sent")))
    cache_path = _cache_path(config)
    cached = _load_cache(cache_path)
    meta = _load_meta(cache_path)
    started = datetime.now()
    scope_key = _scope_key(settings, days)
    since = incremental_since(meta, scope_key, started, settings)
    seen: dict[str, dict] = {}
    if since is not None:
        print("outlook: reading mail since %s (incremental)" % since.strftime("%Y-%m-%d %H:%M"))
        cutoff_aware = cutoff.astimezone()
        for entry_id, row in cached.items():
            try:
                when = datetime.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue
            if (when if when.tzinfo else when.astimezone()) >= cutoff_aware:
                seen[entry_id] = row
    read_from = since or cutoff
    for folder_name, folder, sent in targets:
        field = "[SentOn]" if sent else "[ReceivedTime]"
        restriction = "%s >= '%s'" % (field, _restrict_date(read_from))
        try:
            entry_ids = _table_entry_ids(folder, restriction)
        except Exception:
            entry_ids = None
        if entry_ids is None:
            items = folder.Items.Restrict(restriction)
            for item in items:
                try:
                    parsed = _mail(item, folder_name, sender_smtp=me if sent else None)
                except Exception:
                    continue
                if parsed is not None:
                    emails.append(parsed)
            continue
        for entry_id in entry_ids:
            row = cached.get(entry_id)
            if row is None:
                try:
                    parsed = _mail(namespace.GetItemFromID(entry_id), folder_name, sender_smtp=me if sent else None)
                except Exception:
                    continue
                if parsed is None:
                    continue
                row = _email_to_row(parsed)
            row["folder"] = folder_name
            seen[entry_id] = row
    emails.extend(_row_to_email(row, row.get("folder") or "") for row in seen.values())
    if seen or since is None:
        new_meta = {
            "scope": scope_key,
            "last_read": started.isoformat(timespec="seconds"),
            "last_full": meta.get("last_full", "") if since is not None else started.isoformat(timespec="seconds"),
        }
        _save_cache(cache_path, seen, new_meta)
    return emails


SKIPPED_FOLDER_NAMES = {"deleted items", "junk email", "drafts", "outbox", "conversation history", "sync issues"}
PUBLIC_FOLDER_STORE = 2


def _store_folders(root, prefix: str, skip: list[str]) -> list[tuple[str, object, bool]]:
    """Mail folders of an extra store (Online Archive, PST), skipping deleted/junk/drafts by name."""
    found: list[tuple[str, object, bool]] = []

    def walk(folder, path: str) -> None:
        try:
            children = list(folder.Folders)
        except Exception:
            return
        for child in children:
            try:
                name = str(child.Name)
                is_mail = child.DefaultItemType == MAIL_FOLDER_TYPE
            except Exception:
                continue
            child_path = path + "/" + name
            if not is_mail or name.lower() in SKIPPED_FOLDER_NAMES or _skipped(child_path, name, skip):
                continue
            found.append((child_path, child, name.lower().startswith("sent")))
            walk(child, child_path)

    walk(root, prefix)
    return found


def collect_headers(config: dict, since_days: int, include_archive: bool = False, progress=None) -> list[Email]:
    """Subject, To, sender and date of every mail newer than since_days, without bodies, through the fast Table
    API. For the one-time project import: reads further back than days_back and never touches outlook_cache."""
    settings = config.get("outlook_com") or {}
    namespace = _namespace()
    skip = [str(item) for item in settings.get("skip_folders") or [] if str(item).strip()]
    targets = mailbox_folders(namespace, skip)
    if include_archive:
        try:
            default_store = str(namespace.GetDefaultFolder(FOLDER_INBOX).Store.StoreID)
        except Exception:
            default_store = ""
        for store in list(namespace.Stores):
            try:
                if str(store.StoreID) == default_store or store.ExchangeStoreType == PUBLIC_FOLDER_STORE:
                    continue
                targets.extend(_store_folders(store.GetRootFolder(), str(store.DisplayName), skip))
            except Exception as exc:
                print("outlook: skipped a store (%s)" % exc)
    cutoff = datetime.now() - timedelta(days=int(since_days))
    seen: dict[str, Email] = {}
    for number, (folder_name, folder, sent) in enumerate(targets):
        if progress is not None:
            progress(number, len(targets), folder_name)
        field = "SentOn" if sent else "ReceivedTime"
        try:
            table = folder.GetTable("[%s] >= '%s'" % (field, _restrict_date(cutoff)))
            for column in ("Subject", "To", "SenderName", field):
                try:
                    table.Columns.Add(column)
                except Exception:
                    pass
            while not table.EndOfTable:
                row = table.GetNextRow()
                if not str(row["MessageClass"] or "").upper().startswith("IPM.NOTE"):
                    continue
                when = _local(row[field])
                entry_id = str(row["EntryID"] or "")
                if when is None or not entry_id or entry_id in seen:
                    continue
                seen[entry_id] = Email(
                    message_id="outlook:" + entry_id,
                    subject=str(row["Subject"] or ""),
                    sender=str(row["SenderName"] or ""),
                    to=str(row["To"] or ""),
                    cc="",
                    date=when,
                    body_text="",
                    source_path="outlook:" + folder_name,
                    entry_id=entry_id,
                )
        except Exception as exc:
            print("outlook: could not read %s (%s)" % (folder_name, exc))
    return list(seen.values())


def item_body(entry_id: str) -> str:
    """Body text of one mail item (used for the few assignment emails the import creates projects from)."""
    try:
        return str(_namespace().GetItemFromID(str(entry_id)).Body or "")
    except Exception:
        return ""


INCREMENTAL_OVERLAP = timedelta(days=1)


def _scope_key(settings: dict, days: int) -> str:
    return "|".join(
        [
            scope(settings),
            ",".join(sorted(str(item).lower() for item in settings.get("folders") or [])),
            ",".join(sorted(str(item).lower() for item in settings.get("skip_folders") or [])),
            str(days),
        ]
    )


def incremental_since(meta: dict, scope_key: str, now: datetime, settings: dict) -> Optional[datetime]:
    """Start of an incremental read (last read minus a day of overlap), or None for a full read: first run,
    changed folders/days, incremental: false, or the last full read is older than full_read_hours (default 24)."""
    if settings.get("incremental", True) is False or meta.get("scope") != scope_key:
        return None
    try:
        last_read = datetime.fromisoformat(str(meta.get("last_read") or ""))
        last_full = datetime.fromisoformat(str(meta.get("last_full") or ""))
    except ValueError:
        return None
    try:
        full_hours = float(settings.get("full_read_hours") or 24)
    except (TypeError, ValueError):
        full_hours = 24.0
    if now - last_full > timedelta(hours=full_hours) or last_read > now:
        return None
    return last_read - INCREMENTAL_OVERLAP


CACHE_BODY_LIMIT = 60000


def _cache_path(config: dict):
    from pathlib import Path

    explicit = (config.get("outlook_com") or {}).get("cache_path")
    if explicit:
        return Path(explicit)
    base = config.get("snapshot_path")
    folder = Path(base).parent if base else Path(__file__).resolve().parents[2] / "output"
    return folder / "outlook_cache.json"


def _read_cache_file(path) -> dict:
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("version") == 1 else {}


def _load_cache(path) -> dict:
    items = _read_cache_file(path).get("items")
    return items if isinstance(items, dict) else {}


def _load_meta(path) -> dict:
    meta = _read_cache_file(path).get("meta")
    return meta if isinstance(meta, dict) else {}


def _save_cache(path, items: dict, meta: dict | None = None) -> None:
    import json
    import os

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"version": 1, "meta": meta or {}, "items": items}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        print("outlook: cache not saved (%s)" % exc)


def _email_to_row(email: Email) -> dict:
    return {
        "message_id": email.message_id,
        "subject": email.subject,
        "sender": email.sender,
        "to": email.to,
        "cc": email.cc,
        "date": email.date.isoformat(),
        "body_text": email.body_text[:CACHE_BODY_LIMIT],
        "entry_id": email.entry_id,
    }


def _row_to_email(row: dict, folder_name: str) -> Email:
    return Email(
        message_id=row["message_id"],
        subject=row["subject"],
        sender=row["sender"],
        to=row["to"],
        cc=row["cc"],
        date=datetime.fromisoformat(row["date"]),
        body_text=row["body_text"],
        source_path="outlook:" + folder_name,
        entry_id=row.get("entry_id", ""),
    )


def _table_entry_ids(folder, restriction: str) -> list[str]:
    """Entry IDs of mail items via the fast Table API (one COM call per row instead of ~10 per item)."""
    table = folder.GetTable(restriction)
    ids: list[str] = []
    while not table.EndOfTable:
        row = table.GetNextRow()
        if str(row["MessageClass"] or "").upper().startswith("IPM.NOTE"):
            ids.append(str(row["EntryID"]))
    return ids


def collect_calendar(config: dict, since_days: Optional[int] = None) -> list[Email]:
    settings = config.get("outlook_com") or {}
    if not settings.get("enabled") or not settings.get("calendar"):
        return []
    days = int(since_days or settings.get("days_back") or 120)
    ahead = int(settings.get("calendar_days_ahead") or 60)
    start = datetime.now() - timedelta(days=days)
    end = datetime.now() + timedelta(days=ahead)
    items = _namespace().GetDefaultFolder(FOLDER_CALENDAR).Items
    items.IncludeRecurrences = True
    items.Sort("[Start]")
    items = items.Restrict("[Start] >= '%s' AND [Start] <= '%s'" % (_restrict_date(start), _restrict_date(end)))
    events: list[Email] = []
    for item in items:
        try:
            if getattr(item, "Class", None) != APPOINTMENT_ITEM:
                continue
            when = _local(item.Start)
            if when is None:
                continue
            uid = str(getattr(item, "GlobalAppointmentID", "") or getattr(item, "EntryID", ""))
            events.append(
                Email(
                    message_id="calendar:%s:%s" % (uid, when.date().isoformat()),
                    subject=str(item.Subject or ""),
                    sender=str(getattr(item, "Organizer", "") or ""),
                    to=str(getattr(item, "RequiredAttendees", "") or ""),
                    cc=str(getattr(item, "OptionalAttendees", "") or ""),
                    date=when,
                    body_text=str(getattr(item, "Body", "") or ""),
                    source_path="outlook:Calendar",
                    entry_id=str(getattr(item, "EntryID", "") or ""),
                    end=_local(getattr(item, "End", None)),
                )
            )
        except Exception:
            continue
    return events
