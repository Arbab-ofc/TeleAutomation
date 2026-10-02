import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, utils
from telethon.tl.types import Channel, Chat, User

from app.config import PENDING_AUTH_TTL_SECONDS, SESSIONS_DIR
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self, storage: StorageService, user_id: str) -> None:
        self.storage = storage
        self.user_id = user_id
        self.session_dir = SESSIONS_DIR / user_id
        self.session_file = self.session_dir / "telegram"
        self.client: TelegramClient | None = None
        self._lock = asyncio.Lock()
        self._phone: str | None = None
        self._phone_code_hash: str | None = None
        self._pending_expires_at: datetime | None = None
        self._last_error: str | None = None
        self._state = "Authentication Required"

    async def initialize(self) -> None:
        settings = await self.storage.get_settings()
        if settings.get("api_id") and settings.get("api_hash"):
            try:
                await self._create_client(settings)
                await self.connect()
            except Exception as exc:
                logger.warning("Telegram startup connection failed: %s", exc)
                self._state, self._last_error = "Error", self.error_message(exc)

    async def _create_client(self, settings: dict[str, str]) -> TelegramClient:
        if self.client:
            await self.client.disconnect()
        self.session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.session_dir, 0o700)
        self.client = TelegramClient(str(self.session_file), int(settings["api_id"]), settings["api_hash"])
        return self.client

    async def rebuild_client(self) -> None:
        async with self._lock:
            settings = await self.storage.get_settings()
            if not settings.get("api_id") or not settings.get("api_hash"):
                self.client = None
                self._state = "Authentication Required"
                return
            await self._create_client(settings)
            await self.connect()

    async def connect(self) -> bool:
        if not self.client:
            raise RuntimeError("Telegram API credentials are not configured.")
        self._state = "Connecting"
        await self.client.connect()
        for suffix in (".session", ".session-journal"):
            session_path = Path(str(self.session_file) + suffix)
            if session_path.exists():
                os.chmod(session_path, 0o600)
        authorized = await self.client.is_user_authorized()
        self._state = "Connected" if authorized else "Authentication Required"
        self._last_error = None
        return authorized

    async def status(self) -> dict[str, Any]:
        configured = bool((await self.storage.get_settings()).get("api_id"))
        connected = bool(self.client and self.client.is_connected())
        authorized = False
        account = None
        if connected:
            try:
                authorized = await self.client.is_user_authorized()  # type: ignore[union-attr]
                if authorized:
                    me = await self.client.get_me()  # type: ignore[union-attr]
                    account = self._account(me)
                    self._state = "Connected"
            except Exception as exc:
                self._state, self._last_error = "Error", self.error_message(exc)
        return {"configured": configured, "connected": connected, "authorized": authorized,
                "state": self._state, "account": account, "last_error": self._last_error}

    @staticmethod
    def _account(me: User) -> dict[str, Any]:
        phone = me.phone or ""
        masked = f"+{'*' * max(len(phone) - 4, 0)}{phone[-4:]}" if phone else None
        return {"id": str(me.id), "first_name": me.first_name, "last_name": me.last_name,
                "username": me.username, "phone": masked}

    async def send_code(self, phone: str) -> None:
        async with self._lock:
            if not self.client:
                raise RuntimeError("Configure Telegram API credentials first.")
            if not self.client.is_connected():
                await self.client.connect()
            result = await self.client.send_code_request(phone)
            self._phone, self._phone_code_hash = phone, result.phone_code_hash
            self._pending_expires_at = datetime.now(timezone.utc) + timedelta(seconds=PENDING_AUTH_TTL_SECONDS)

    async def verify_code(self, code: str) -> str:
        async with self._lock:
            if (not self.client or not self._phone or not self._phone_code_hash or
                    not self._pending_expires_at or self._pending_expires_at <= datetime.now(timezone.utc)):
                self._clear_login()
                raise RuntimeError("Request a verification code first.")
            try:
                await self.client.sign_in(phone=self._phone, code=code, phone_code_hash=self._phone_code_hash)
            except errors.SessionPasswordNeededError:
                return "PASSWORD_REQUIRED"
            self._clear_login()
            self._state = "Connected"
            return "AUTHORIZED"

    async def verify_password(self, password: str) -> None:
        async with self._lock:
            if (not self.client or not self._phone or not self._pending_expires_at or
                    self._pending_expires_at <= datetime.now(timezone.utc)):
                self._clear_login()
                raise RuntimeError("Telegram client is not initialized.")
            await self.client.sign_in(password=password)
            self._clear_login()
            self._state = "Connected"

    def _clear_login(self) -> None:
        self._phone = self._phone_code_hash = None
        self._pending_expires_at = None

    async def dialogs(self) -> list[dict[str, Any]]:
        await self.require_authorized()
        result = []
        async for dialog in self.client.iter_dialogs():  # type: ignore[union-attr]
            entity = dialog.entity
            destination_type = None
            if isinstance(entity, Chat):
                destination_type = "group"
            elif isinstance(entity, Channel):
                destination_type = "supergroup" if entity.megagroup else "channel"
            elif isinstance(entity, User):
                destination_type = "private"
            if not destination_type:
                continue
            can_send = self._can_send(entity)
            result.append({"id": str(utils.get_peer_id(entity)), "title": dialog.name or "Untitled",
                           "username": getattr(entity, "username", None), "type": destination_type,
                           "can_send": can_send})
        order = {"group": 0, "supergroup": 1, "channel": 2, "private": 3}
        return sorted(result, key=lambda item: (not item["can_send"], order[item["type"]], item["title"].casefold()))

    @staticmethod
    def _can_send(entity: Any) -> bool:
        if getattr(entity, "left", False) or getattr(entity, "deactivated", False):
            return False
        if isinstance(entity, Channel):
            if entity.broadcast and not (getattr(entity, "creator", False) or getattr(entity, "admin_rights", None)):
                return False
            rights = getattr(entity, "banned_rights", None)
            return not bool(rights and getattr(rights, "send_messages", False))
        return True

    async def resolve_writable_dialog(self, chat_id: str) -> dict[str, Any]:
        for item in await self.dialogs():
            if item["id"] == chat_id:
                if not item["can_send"]:
                    raise PermissionError("You cannot send messages to this destination.")
                return item
        raise LookupError("The selected destination is unavailable. Refresh destinations and try again.")

    async def send_message(self, chat_id: str, message: str) -> None:
        await self.require_authorized()
        await self.client.send_message(int(chat_id), message)  # type: ignore[union-attr]

    async def require_authorized(self) -> None:
        if not self.client or not self.client.is_connected():
            raise ConnectionError("Telegram is disconnected.")
        if not await self.client.is_user_authorized():
            raise PermissionError("Telegram authorization is required.")

    async def reconnect(self) -> None:
        async with self._lock:
            if not self.client:
                settings = await self.storage.get_settings()
                if not settings.get("api_id") or not settings.get("api_hash"):
                    raise RuntimeError("Telegram API credentials are not configured.")
                await self._create_client(settings)
                await self.connect()
                return
            await self.client.disconnect()
            await self.connect()

    async def logout(self) -> None:
        async with self._lock:
            if self.client:
                try:
                    if self.client.is_connected() and await self.client.is_user_authorized():
                        await self.client.log_out()
                    else:
                        await self.client.disconnect()
                finally:
                    self.client = None
            self._clear_login()
            for suffix in (".session", ".session-journal"):
                path = Path(str(self.session_file) + suffix)
                path.unlink(missing_ok=True)
            self._state = "Authentication Required"

    async def disconnect(self) -> None:
        if self.client:
            await self.client.disconnect()

    @property
    def pending_login(self) -> bool:
        return bool(self._phone and self._pending_expires_at and self._pending_expires_at > datetime.now(timezone.utc))

    @staticmethod
    def error_message(exc: Exception) -> str:
        mapping = {
            errors.PhoneCodeInvalidError: "Invalid verification code.",
            errors.PhoneCodeExpiredError: "The verification code expired. Request a new one.",
            errors.PasswordHashInvalidError: "Incorrect Telegram 2FA password.",
            errors.ApiIdInvalidError: "Telegram rejected the API credentials.",
            errors.PhoneNumberInvalidError: "The phone number is invalid.",
            errors.ChatWriteForbiddenError: "You cannot send messages to this group.",
            errors.UserBannedInChannelError: "Your account is restricted from sending to this destination.",
            errors.ChannelPrivateError: "This destination is private or no longer accessible.",
            errors.MessageTooLongError: "The message is too long for Telegram.",
        }
        for error_type, message in mapping.items():
            if isinstance(exc, error_type):
                return message
        if isinstance(exc, errors.FloodWaitError):
            return f"Telegram requested a {exc.seconds}-second wait."
        if isinstance(exc, errors.SlowModeWaitError):
            return f"Group slow mode active. Wait {exc.seconds} seconds."
        if isinstance(exc, (ConnectionError, OSError)):
            return "Could not connect to Telegram."
        if isinstance(exc, (RuntimeError, LookupError, PermissionError, ValueError)):
            return str(exc)
        return "Telegram could not complete the request."
