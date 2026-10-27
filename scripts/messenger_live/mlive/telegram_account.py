"""The harness's Telegram device: TDLib linked to the owner's account by a QR code, like Signal's.

The owner scans the `tg://login` QR code once (Settings → Devices → Link Desktop Device); the
session is kept under `~/.config/dreamference/telegram-live/` (0700, files 0600) and shows in the
owner's device list as "ling test", where it can be revoked. Telegram's test servers are not used:
test accounts were disabled in 2024 (tdlib/td#3083).

Privacy (owner's rules): the harness opens and reads one chat, the test bot's; it never lists
chats, never reads another chat's history, never downloads media and never sends anywhere else.
TDLib keeps no message database and no file database, and automatic downloads are switched off.
Updates about any other chat are dropped where they arrive (`BotChat.accepts`), unread and unlogged.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Final

from .paths import cache_dir, config_dir, private_dir, telegram_dir
from .tdlib import TdClient, TdError, ensure_library, update_chat_id

DEVICE_NAME: Final = "ling test"


class NeedsOwner(Exception):
    """The session is gone or needs the owner (a re-link, or a cloud password): never a test failure."""


def load_state(path: Path) -> dict[str, Any]:
    """Reads the harness's private state (`{}` when absent or unreadable)."""
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Writes the private state 0600, replacing the file whole."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = path.with_suffix(".part")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(state, out, indent=1)
    partial.rename(path)


def tdlib_parameters(database: Path, api: dict[str, Any]) -> dict[str, Any]:
    """The `setTdlibParameters` request: production servers, no message or file database."""
    return {
        "@type": "setTdlibParameters",
        "use_test_dc": False,
        "database_directory": str(database),
        "files_directory": str(database / "files"),
        "use_file_database": False,
        "use_chat_info_database": False,
        "use_message_database": False,
        "use_secret_chats": False,
        "api_id": int(api["api_id"]),
        "api_hash": str(api["api_hash"]),
        "system_language_code": "en",
        "device_model": DEVICE_NAME,
        "system_version": "",
        "application_version": "1",
    }


def no_auto_download() -> list[dict[str, Any]]:
    """The `setAutoDownloadSettings` requests that switch every automatic download off."""
    settings = {
        "@type": "autoDownloadSettings",
        "is_auto_download_enabled": False,
        "max_photo_file_size": 0,
        "max_video_file_size": 0,
        "max_other_file_size": 0,
        "video_upload_bitrate": 0,
        "preload_large_videos": False,
        "preload_next_audio": False,
        "preload_stories": False,
        "use_less_data_for_calls": True,
    }
    kinds = ("networkTypeMobile", "networkTypeMobileRoaming", "networkTypeWiFi", "networkTypeOther")
    return [
        {"@type": "setAutoDownloadSettings", "settings": settings, "type": {"@type": kind}}
        for kind in kinds
    ]


def write_qr(link: str, png: Path) -> None:
    """Writes the login link as a QR code PNG (0600), replacing the previous one whole."""
    partial = png.with_name(png.stem + ".part.png")
    subprocess.run(["qrencode", "-o", str(partial), "-s", "8", "-m", "3", link], check=True)
    Path(partial).chmod(0o600)
    partial.rename(png)


def sign_in(
    client: TdClient, database: Path, api: dict[str, Any], qr_png: Path | None, timeout: float
) -> dict[str, Any]:
    """Drives TDLib's authorization to Ready.

    With a stored session this needs nothing. Without one it needs `qr_png`: the QR code is
    written there and rewritten each time Telegram renews it (about every 30 seconds) until the
    owner scans it or `timeout` passes.

    Args:
        client: A fresh client.
        database: TDLib's database folder (0700).
        api: `{"api_id", "api_hash"}`.
        qr_png: Where the login QR code goes, or None for "never ask the owner".
        timeout: Seconds for the whole sign-in.

    Returns:
        The account's own user (`getMe`).

    Raises:
        NeedsOwner: No session and no QR allowed, a cloud password asked, or no scan in time.
    """
    while True:
        update = client.wait_for(lambda u: u.get("@type") == "updateAuthorizationState", timeout)
        if update is None:
            raise NeedsOwner("owner re-link needed: the QR code was not scanned in time")
        state = update["authorization_state"]
        kind = state["@type"]
        try:
            if kind == "authorizationStateWaitTdlibParameters":
                client.call(tdlib_parameters(database, api))
            elif kind == "authorizationStateWaitPhoneNumber":
                if qr_png is None:
                    raise NeedsOwner("owner re-link needed: the Telegram session is gone")
                client.call({"@type": "requestQrCodeAuthentication", "other_user_ids": []})
            elif kind == "authorizationStateWaitOtherDeviceConfirmation":
                if qr_png is None:
                    raise NeedsOwner("owner re-link needed: the Telegram session is gone")
                write_qr(state["link"], qr_png)
            elif kind == "authorizationStateWaitPassword":
                raise NeedsOwner(
                    "the account asks for its cloud password; the harness never takes it"
                )
            elif kind == "authorizationStateReady":
                if qr_png is not None and qr_png.exists():
                    qr_png.unlink()
                for request in no_auto_download():
                    client.call(request)
                return client.call({"@type": "getMe"})
            elif kind in (
                "authorizationStateClosed",
                "authorizationStateClosing",
                "authorizationStateLoggingOut",
            ):
                raise NeedsOwner(f"owner re-link needed: the Telegram session ended ({kind})")
            elif kind in (
                "authorizationStateWaitCode",
                "authorizationStateWaitRegistration",
                "authorizationStateWaitEmailAddress",
                "authorizationStateWaitEmailCode",
            ):
                raise NeedsOwner(
                    f"owner re-link needed: Telegram asked for {kind}, which the harness does not do"
                )
        except TdError as err:
            raise NeedsOwner(
                f"owner re-link needed: Telegram refused the sign-in at {kind}: {err.message}"
            ) from err


class BotChat:
    """The one chat the harness may touch: the private chat with the test bot."""

    def __init__(self, client: TdClient, username: str) -> None:
        """Finds the bot by its public username and opens that chat only.

        Args:
            client: A signed-in client.
            username: The bot's username, without `@`.
        """
        self.client = client
        chat = client.call({"@type": "searchPublicChat", "username": username})
        if chat.get("type", {}).get("@type") != "chatTypePrivate":
            raise RuntimeError(f"@{username} is not a private chat with a bot")
        self.chat_id: int = chat["id"]
        self.bot_user_id: int = chat["type"]["user_id"]
        client.allowed_chat = self.chat_id
        client.call({"@type": "openChat", "chat_id": self.chat_id})

    def accepts(self, update: dict[str, Any]) -> bool:
        """Says whether an update concerns the bot's chat (everything else is dropped unread)."""
        return update_chat_id(update) == self.chat_id


def open_session(qr_png: Path | None, timeout: float) -> tuple[TdClient, dict[str, Any]]:
    """Starts TDLib on the stored session (or links a new one through `qr_png`).

    Returns:
        The signed-in client and the account's own user.

    Raises:
        NeedsOwner: See `sign_in`.
    """
    folder = private_dir(telegram_dir())
    api = json.loads((config_dir() / "telegram-api.json").read_text())
    client = TdClient(ensure_library(private_dir(cache_dir())), folder / "tdlib.log")
    try:
        me = sign_in(client, private_dir(folder / "tdlib"), api, qr_png, timeout)
    except BaseException:
        client.close()
        raise
    return client, me
