import asyncio

from app.services.storage_service import StorageService
from app.services.telegram_service import TelegramService


class TelegramClientManager:
    def __init__(self, storage: StorageService) -> None:
        self.storage = storage
        self._services: dict[str, TelegramService] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._manager_lock = asyncio.Lock()

    async def get(self, user_id: str) -> TelegramService:
        async with self._manager_lock:
            service = self._services.get(user_id)
            if service:
                return service
            lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if user_id in self._services:
                return self._services[user_id]
            service = TelegramService(self.storage, user_id)
            await service.initialize()
            async with self._manager_lock:
                self._services[user_id] = service
            return service

    async def disconnect(self, user_id: str, *, remove: bool = False) -> None:
        async with self._manager_lock:
            service = self._services.get(user_id)
        if service:
            await service.disconnect()
        if remove:
            async with self._manager_lock:
                self._services.pop(user_id, None)
                self._locks.pop(user_id, None)

    async def rebuild_all(self) -> None:
        async with self._manager_lock:
            services = list(self._services.values())
        await asyncio.gather(*(service.rebuild_client() for service in services), return_exceptions=False)

    async def shutdown(self) -> None:
        async with self._manager_lock:
            services = list(self._services.values())
            self._services.clear()
            self._locks.clear()
        await asyncio.gather(*(service.disconnect() for service in services), return_exceptions=True)

    @property
    def active_count(self) -> int:
        return len(self._services)
