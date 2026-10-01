import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import MESSAGES_FILE, SETTINGS_FILE, TELEGRAM_API_HASH, TELEGRAM_API_ID


class StorageService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._ensure_file(SETTINGS_FILE, {})
        self._ensure_file(MESSAGES_FILE, [])

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

    async def list_messages(self) -> list[dict[str, str]]:
        async with self._lock:
            return self._read_sync(MESSAGES_FILE, [])

    async def create_message(self, name: str, content: str) -> dict[str, str]:
        async with self._lock:
            items = self._read_sync(MESSAGES_FILE, [])
            self._assert_unique(items, name)
            item = {"id": str(uuid4()), "name": name, "content": content}
            items.append(item)
            self._write_sync(MESSAGES_FILE, items)
            return item

    async def update_message(self, item_id: str, name: str, content: str) -> dict[str, str]:
        async with self._lock:
            items = self._read_sync(MESSAGES_FILE, [])
            self._assert_unique(items, name, item_id)
            for item in items:
                if item.get("id") == item_id:
                    item.update(name=name, content=content)
                    self._write_sync(MESSAGES_FILE, items)
                    return item
            raise KeyError("Saved message not found.")

    async def delete_message(self, item_id: str) -> None:
        async with self._lock:
            items = self._read_sync(MESSAGES_FILE, [])
            filtered = [item for item in items if item.get("id") != item_id]
            if len(filtered) == len(items):
                raise KeyError("Saved message not found.")
            self._write_sync(MESSAGES_FILE, filtered)

    @staticmethod
    def _assert_unique(items: list[dict[str, str]], name: str, exclude_id: str | None = None) -> None:
        if any(i.get("name", "").casefold() == name.casefold() and i.get("id") != exclude_id for i in items):
            raise ValueError("A saved message with this name already exists.")
