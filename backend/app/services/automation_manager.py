import asyncio

from app.services.automation_service import AutomationService
from app.services.runtime_store import RuntimeStore
from app.services.telegram_manager import TelegramClientManager
from app.utils.logger import EventLog


class AutomationManager:
    def __init__(self, telegram: TelegramClientManager, runtime: RuntimeStore, licenses) -> None:
        self.telegram = telegram
        self.runtime = runtime
        self.licenses = licenses
        self._services: dict[str, AutomationService] = {}
        self._events: dict[str, EventLog] = {}
        self._lock = asyncio.Lock()

    async def get(self, user_id: str) -> AutomationService:
        async with self._lock:
            current = self._services.get(user_id)
            if current:
                return current
        telegram = await self.telegram.get(user_id)
        async with self._lock:
            current = self._services.get(user_id)
            if current:
                return current
            events = EventLog(user_id)
            service = AutomationService(telegram, events, self.runtime, self.licenses, user_id)
            self._events[user_id] = events
            self._services[user_id] = service
            return service

    async def events(self, user_id: str) -> EventLog:
        await self.get(user_id)
        return self._events[user_id]

    async def stop(self, user_id: str) -> dict:
        return await (await self.get(user_id)).stop()

    async def shutdown(self) -> None:
        async with self._lock:
            services = list(self._services.values())
        await asyncio.gather(*(service.shutdown() for service in services), return_exceptions=True)

    async def stop_all(self) -> None:
        async with self._lock:
            services = list(self._services.values())
        await asyncio.gather(*(service.stop() for service in services), return_exceptions=True)

    async def recover_eligible(self) -> int:
        """Reconnect only users with explicitly resumable persisted jobs."""
        recovered = 0
        for user_id in await asyncio.to_thread(self.runtime.user_ids):
            job = await asyncio.to_thread(self.runtime.job, user_id)
            if not isinstance(job, dict) or job.get("desired_state") != "RUNNING" or job.get("state") not in {"RUNNING", "WAITING"}:
                continue
            if await (await self.get(user_id)).recover(enabled=True):
                recovered += 1
        return recovered

    @property
    def active_count(self) -> int:
        return sum(1 for service in self._services.values() if service.running)
