"""
Read-only Google Drive and Google Calendar for Puffin's apps.

Specified in specs/DREAMFERENCE_PUFFIN_APPS.md §9 and §9a.

The Google service (`gmail_search_service.py`, container `dreamference-gmail`) calls this with
the accounts it holds tokens for; `puffin apps serve drive|calendar` reaches it through the
service's `/drive/…` and `/calendar/…` endpoints. It is staged into the container beside the
service, so it imports nothing but the standard library.

**Read-only by construction.** GNOME's OAuth client is refused the read-only scopes, so the tokens
carry the full `drive` and `calendar` scopes (§11.1 item 5). The only requests made here are
`files.list`, `files.get`, `files.export`, `calendarList.list`, `events.list` and `events.get`:
there is no method that writes, and none is to be added.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Final, List, Optional, Tuple

DRIVE_SCOPE: Final[str] = "https://www.googleapis.com/auth/drive"
CALENDAR_SCOPE: Final[str] = "https://www.googleapis.com/auth/calendar"

DRIVE_API: Final[str] = "https://www.googleapis.com/drive/v3"
CALENDAR_API: Final[str] = "https://www.googleapis.com/calendar/v3"

# The same caps as Gmail's: enough for the model to choose from, and a body it can read whole.
MAX_RESULTS: Final[int] = 20
MAX_EVENTS: Final[int] = 50
MAX_TEXT_CHARACTERS: Final[int] = 20_000

# A plain-text file is downloaded whole only below this; Google refuses exports over 10 MB anyway.
MAX_DOWNLOAD_BYTES: Final[int] = 1024 * 1024

# Google Workspace types and what each is exported as.
EXPORT_TYPES: Final[Dict[str, str]] = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}

# What a search answers with, per file and per event.
FILE_FIELDS: Final[str] = (
    "files(id,name,mimeType,modifiedTime,owners(emailAddress),driveId),nextPageToken"
)
EVENT_FIELDS: Final[str] = (
    "items(id,summary,start,end,location,organizer(email),attendees(email),status)"
)

REQUEST_TIMEOUT_SECONDS: Final[int] = 30

Account = Dict[str, Any]
Opener = Callable[..., Any]


class GoogleWorkspaceReader:
    """
    Searches and reads Drive files and Calendar events, read-only.

    Every connected account that holds the scope is asked.
    """

    # Replaced in tests; the service never changes it.
    opener: Opener = staticmethod(urllib.request.urlopen)

    @classmethod
    def holding(cls, accounts: List[Account], scope: str) -> List[Account]:
        """
        Picks the accounts whose grant includes a scope.

        Args:
            accounts (List[Account]): `email`, `access_token` and `scopes` per account.
            scope (str): The scope the request needs.

        Returns:
            List[Account]: Those accounts, in the order given.
        """
        return [account for account in accounts if scope in (account.get("scopes") or [])]

    @classmethod
    def _get(cls, url: str, token: str, raw: bool = False) -> Tuple[int, Any]:
        """
        Sends one authenticated GET.

        Args:
            url (str): The full URL.
            token (str): The account's access token.
            raw (bool): Return the body as bytes rather than parsed JSON.

        Returns:
            Tuple[int, Any]: The status and the body; on HTTP errors, Google's error message.
        """
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with cls.opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read(MAX_DOWNLOAD_BYTES + 1)
                return response.status, body if raw else json.loads(body or b"{}")
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read() or b"{}").get("error", {}).get("message")
            except ValueError:
                message = None
            return error.code, {"error": message or f"HTTP {error.code}"}

    @classmethod
    def _drive_query(cls, terms: str) -> str:
        """
        Builds a Drive `q` that matches names or content and leaves out the bin.

        Args:
            terms (str): What the user looks for, as plain words.

        Returns:
            str: A Drive query with the words quoted.
        """
        quoted = terms.replace("\\", "\\\\").replace("'", "\\'")
        return f"(name contains '{quoted}' or fullText contains '{quoted}') and trashed = false"

    @classmethod
    def drive_search(cls, accounts: List[Account], terms: str, limit: int) -> Dict[str, Any]:
        """
        Searches My Drive and the shared drives of every account holding the Drive scope.

        Args:
            accounts (List[Account]): Connected accounts with fresh access tokens.
            terms (str): Words to look for in names and content.
            limit (int): Results wanted in all, clamped to `MAX_RESULTS`.

        Returns:
            Dict[str, Any]: `files` (ids are `"<account>|<file id>"`), plus `errors` for accounts
                that failed, or `error` when no account holds the scope.
        """
        holders = cls.holding(accounts, DRIVE_SCOPE)
        if not holders:
            return {"error": "No connected account has Google Drive access. Connect it with /apps."}
        limit = max(1, min(limit, MAX_RESULTS))
        query = urllib.parse.urlencode(
            {
                "q": cls._drive_query(terms),
                "pageSize": limit,
                "fields": FILE_FIELDS,
                "corpora": "allDrives",
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "orderBy": "modifiedTime desc",
            }
        )
        files: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        for account in holders:
            status, body = cls._get(f"{DRIVE_API}/files?{query}", account["access_token"])
            if status != 200:
                errors.append(
                    {"account": account["email"], "error": body.get("error", f"HTTP {status}")}
                )
                continue
            for item in body.get("files", []):
                files.append(
                    {
                        "id": f"{account['email']}|{item.get('id', '')}",
                        "account": account["email"],
                        "name": item.get("name", ""),
                        "type": item.get("mimeType", ""),
                        "modified": item.get("modifiedTime", ""),
                        "owner": ", ".join(
                            o.get("emailAddress", "") for o in item.get("owners") or []
                        ),
                        "shared_drive": item.get("driveId", ""),
                    }
                )
        answer: Dict[str, Any] = {"files": files[:limit]}
        if errors:
            answer["errors"] = errors
        return answer

    @classmethod
    def _split(
        cls, accounts: List[Account], scope: str, qualified: str
    ) -> Tuple[Optional[Account], str]:
        """
        Resolves an `"<account>|<id>"` id to its account; a bare id goes to the first holder.

        Args:
            accounts (List[Account]): Connected accounts.
            scope (str): The scope the account must hold.
            qualified (str): The id as a search returned it.

        Returns:
            Tuple[Optional[Account], str]: The account (None when none holds the scope) and the id.
        """
        holders = cls.holding(accounts, scope)
        email, _, bare = qualified.rpartition("|")
        if email:
            return next((a for a in holders if a["email"] == email), None), bare
        return (holders[0] if holders else None), bare

    @classmethod
    def drive_read(cls, accounts: List[Account], qualified_id: str) -> Dict[str, Any]:
        """
        Reads one file as text: Docs and Slides exported as text, Sheets as CSV, `text/*` as is.

        Args:
            accounts (List[Account]): Connected accounts with fresh access tokens.
            qualified_id (str): `"<account>|<file id>"`, as `drive_search` returned it.

        Returns:
            Dict[str, Any]: `id`, `name`, `type`, `modified` and `text` (at most
                `MAX_TEXT_CHARACTERS`), or `error`; binary files are refused with their type.
        """
        account, file_id = cls._split(accounts, DRIVE_SCOPE, qualified_id)
        if account is None or not file_id:
            return {"error": "No connected account with Drive access holds that file."}
        token = account["access_token"]
        quoted = urllib.parse.quote(file_id, safe="")
        status, meta = cls._get(
            f"{DRIVE_API}/files/{quoted}?supportsAllDrives=true&fields=id,name,mimeType,modifiedTime,size",
            token,
        )
        if status != 200:
            return {"error": meta.get("error", f"HTTP {status}")}
        mime = meta.get("mimeType", "")
        if mime in EXPORT_TYPES:
            url = f"{DRIVE_API}/files/{quoted}/export?" + urllib.parse.urlencode(
                {"mimeType": EXPORT_TYPES[mime]}
            )
        elif mime.startswith("text/") and int(meta.get("size") or 0) <= MAX_DOWNLOAD_BYTES:
            url = f"{DRIVE_API}/files/{quoted}?alt=media&supportsAllDrives=true"
        else:
            return {
                "error": f"{meta.get('name', 'The file')} is {mime or 'a binary file'} "
                f"({meta.get('size', '?')} bytes), which Puffin does not read as text."
            }
        status, body = cls._get(url, token, raw=True)
        if status != 200:
            return {
                "error": body.get("error", f"HTTP {status}")
                if isinstance(body, dict)
                else f"HTTP {status}"
            }
        text = body.decode("utf-8", errors="replace")
        return {
            "id": qualified_id,
            "account": account["email"],
            "name": meta.get("name", ""),
            "type": mime,
            "modified": meta.get("modifiedTime", ""),
            "text": text[:MAX_TEXT_CHARACTERS],
        }

    @classmethod
    def _calendars(cls, account: Account) -> Tuple[List[str], Optional[str]]:
        """
        Lists one account's calendars.

        Args:
            account (Account): A connected account.

        Returns:
            Tuple[List[str], Optional[str]]: Calendar ids, and an error when the list failed.
        """
        status, body = cls._get(
            f"{CALENDAR_API}/users/me/calendarList?fields=items(id)", account["access_token"]
        )
        if status != 200:
            return [], body.get("error", f"HTTP {status}")
        return [item["id"] for item in body.get("items", []) if item.get("id")], None

    @classmethod
    def _event(cls, account: Account, calendar: str, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Flattens one event to the columns the tools show.

        Args:
            account (Account): The account it came from.
            calendar (str): Its calendar id.
            item (Dict[str, Any]): Google's event resource.

        Returns:
            Dict[str, Any]: id, account, calendar, start, end, title, location, organiser,
                attendee count (and description when present).
        """

        def when(key: str) -> str:
            moment = item.get(key) or {}
            return moment.get("dateTime") or moment.get("date") or ""

        event = {
            "id": item.get("id", ""),
            "account": account["email"],
            "calendar": f"{account['email']}|{calendar}",
            "start": when("start"),
            "end": when("end"),
            "title": item.get("summary", ""),
            "location": item.get("location", ""),
            "organiser": (item.get("organizer") or {}).get("email", ""),
            "attendees": len(item.get("attendees") or []),
        }
        if item.get("description"):
            event["description"] = item["description"][:MAX_TEXT_CHARACTERS]
        return event

    @classmethod
    def calendar_events(
        cls,
        accounts: List[Account],
        start: str,
        end: str,
        calendar: str = "",
        terms: str = "",
        limit: int = 25,
    ) -> Dict[str, Any]:
        """
        Lists (or searches) events between two times.

        Every calendar of every account holding the Calendar scope is asked.

        Args:
            accounts (List[Account]): Connected accounts with fresh access tokens.
            start (str): RFC 3339 lower bound.
            end (str): RFC 3339 upper bound.
            calendar (str): `"<account>|<calendar id>"` or a bare calendar id; empty for all.
            terms (str): Free-text search (`q`); empty to list.
            limit (int): Events wanted in all, clamped to `MAX_EVENTS`.

        Returns:
            Dict[str, Any]: `events`, earliest first, plus `errors` for calendars that failed.
        """
        holders = cls.holding(accounts, CALENDAR_SCOPE)
        if not holders:
            return {
                "error": "No connected account has Google Calendar access. Connect it with /apps."
            }
        limit = max(1, min(limit, MAX_EVENTS))
        params = {
            "timeMin": start,
            "timeMax": end,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": limit,
            "fields": EVENT_FIELDS,
        }
        if terms:
            params["q"] = terms
        events: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        wanted_account, _, wanted_calendar = calendar.rpartition("|")
        for account in holders:
            if wanted_account and account["email"] != wanted_account:
                continue
            if wanted_calendar:
                calendars, error = [wanted_calendar], None
            else:
                calendars, error = cls._calendars(account)
            if error:
                errors.append({"account": account["email"], "error": error})
            for calendar_id in calendars:
                url = (
                    f"{CALENDAR_API}/calendars/{urllib.parse.quote(calendar_id, safe='')}/events?"
                    + urllib.parse.urlencode(params)
                )
                status, body = cls._get(url, account["access_token"])
                if status != 200:
                    errors.append(
                        {
                            "account": account["email"],
                            "error": f"{calendar_id}: {body.get('error')}",
                        }
                    )
                    continue
                events.extend(
                    cls._event(account, calendar_id, item) for item in body.get("items", [])
                )
        events.sort(key=lambda event: event["start"])
        answer: Dict[str, Any] = {"events": events[:limit]}
        if errors:
            answer["errors"] = errors
        return answer

    @classmethod
    def calendar_event(
        cls, accounts: List[Account], calendar: str, event_id: str
    ) -> Dict[str, Any]:
        """
        Reads one event with its description.

        Args:
            accounts (List[Account]): Connected accounts with fresh access tokens.
            calendar (str): The calendar id an earlier answer gave.
            event_id (str): The event id.

        Returns:
            Dict[str, Any]: The event's columns and `description`, or `error`.
        """
        account, calendar_id = cls._split(accounts, CALENDAR_SCOPE, calendar)
        if account is None:
            return {"error": "No connected account with Calendar access holds that calendar."}
        url = (
            f"{CALENDAR_API}/calendars/{urllib.parse.quote(calendar_id, safe='')}/events/"
            f"{urllib.parse.quote(event_id, safe='')}"
        )
        status, body = cls._get(url, account["access_token"])
        if status != 200:
            return {"error": body.get("error", f"HTTP {status}")}
        return cls._event(account, calendar_id, body)
