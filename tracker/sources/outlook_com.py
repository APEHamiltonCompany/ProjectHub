from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..models import Email

MAIL_ITEM = 43
APPOINTMENT_ITEM = 26
FOLDER_INBOX = 6
FOLDER_SENT = 5
FOLDER_CALENDAR = 9
DEFAULT_FOLDERS = {"inbox": FOLDER_INBOX, "sent items": FOLDER_SENT, "sent": FOLDER_SENT}
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


def collect(config: dict, since_days: Optional[int] = None) -> list[Email]:
    settings = config.get("outlook_com") or {}
    if not settings.get("enabled"):
        return []
    days = int(since_days or settings.get("days_back") or 120)
    cutoff = datetime.now() - timedelta(days=days)
    namespace = _namespace()
    me = _me(namespace)
    emails: list[Email] = []
    for folder_name in settings.get("folders") or []:
        folder = _folder(namespace, folder_name)
        if folder is None:
            print("outlook: folder not found: %s" % folder_name)
            continue
        sent = folder_name.strip().lower().startswith("sent")
        field = "[SentOn]" if sent else "[ReceivedTime]"
        items = folder.Items.Restrict("%s >= '%s'" % (field, _restrict_date(cutoff)))
        for item in items:
            try:
                parsed = _mail(item, folder_name, sender_smtp=me if sent else None)
            except Exception:
                continue
            if parsed is not None:
                emails.append(parsed)
    return emails


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
