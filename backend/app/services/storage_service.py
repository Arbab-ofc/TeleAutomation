import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import MESSAGES_FILE, SETTINGS_FILE, TELEGRAM_API_HASH, TELEGRAM_API_ID

try:
    import firebase_admin
    from firebase_admin import db
except ImportError:
    firebase_admin = None
    db = None


class StorageService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._ensure_file(SETTINGS_FILE, {})

    @staticmethod
    def _ensure_file(path: Path, default: Any) -> None:
        if not path.exists():
            StorageService._write_sync(path, default)

    @staticmethod
    def _read_sync(path: Path, default: Any) -> Any:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, type(default)):
                raise ValueError("Unexpected JSON root type")
            return data
        except (OSError, json.JSONDecodeError, ValueError):
            StorageService._write_sync(path, default)
            return default.copy() if hasattr(default, "copy") else default

    @staticmethod
    def _write_sync(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)

    async def get_settings(self) -> dict[str, str]:
        async with self._lock:
            settings = self._read_sync(SETTINGS_FILE, {})
            if not settings.get("api_id") and TELEGRAM_API_ID and TELEGRAM_API_HASH:
                return {"api_id": TELEGRAM_API_ID, "api_hash": TELEGRAM_API_HASH}
            return settings

    async def save_settings(self, api_id: str, api_hash: str) -> None:
        async with self._lock:
            self._write_sync(SETTINGS_FILE, {"api_id": api_id, "api_hash": api_hash})

    @property
    def _firebase(self) -> bool:
        return bool(firebase_admin and firebase_admin._apps and db)

    @staticmethod
    def _user_file(user_id: str) -> Path:
        return MESSAGES_FILE.parent / "users" / user_id / "messages.json"

    def _messages_ref(self, user_id: str):
        return db.reference("users").child(user_id).child("messages")

    def _read_messages(self, user_id: str) -> list[dict[str, str]]:
        if self._firebase:
            raw = self._messages_ref(user_id).get() or {}
            if isinstance(raw, dict):
                return [{"id": str(key), **value} for key, value in raw.items() if isinstance(value, dict)]
            return []
        return self._read_sync(self._user_file(user_id), [])

    def _write_messages(self, user_id: str, items: list[dict[str, str]]) -> None:
        if self._firebase:
            self._messages_ref(user_id).set({item["id"]: {"name": item["name"], "content": item["content"]} for item in items})
        else:
            self._write_sync(self._user_file(user_id), items)

    async def list_messages(self, user_id: str) -> list[dict[str, str]]:
        async with self._lock:
            return self._read_messages(user_id)

    async def create_message(self, user_id: str, name: str, content: str) -> dict[str, str]:
        async with self._lock:
            items = self._read_messages(user_id)
            self._assert_unique(items, name)
            item = {"id": str(uuid4()), "name": name, "content": content}
            items.append(item)
            self._write_messages(user_id, items)
            return item

    async def update_message(self, user_id: str, item_id: str, name: str, content: str) -> dict[str, str]:
        async with self._lock:
            items = self._read_messages(user_id)
            self._assert_unique(items, name, item_id)
            for item in items:
                if item.get("id") == item_id:
                    item.update(name=name, content=content)
                    self._write_messages(user_id, items)
                    return item
            raise KeyError("Saved message not found.")

    async def delete_message(self, user_id: str, item_id: str) -> None:
        async with self._lock:
            items = self._read_messages(user_id)
            filtered = [item for item in items if item.get("id") != item_id]
            if len(filtered) == len(items):
                raise KeyError("Saved message not found.")
            self._write_messages(user_id, filtered)

    @staticmethod
    def _assert_unique(items: list[dict[str, str]], name: str, exclude_id: str | None = None) -> None:
        if any(i.get("name", "").casefold() == name.casefold() and i.get("id") != exclude_id for i in items):
            raise ValueError("A saved message with this name already exists.")
