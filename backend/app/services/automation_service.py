import asyncio
from datetime import datetime, time, timedelta, timezone
from typing import Any

from telethon import errors

from app.config import AUTOMATION_RESUME_ON_START, MAX_SEND_RETRIES, MIN_INTERVAL_SECONDS, RETRY_BACKOFF_SECONDS
from app.models.schemas import AutomationState
from app.services.runtime_store import RuntimeStore
from app.services.telegram_service import TelegramService
from app.utils.logger import EventLog


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AutomationService:
    def __init__(self, telegram: TelegramService, events: EventLog, runtime: RuntimeStore, licenses: Any) -> None:
        self.telegram, self.events, self.runtime, self.licenses = telegram, events, runtime, licenses
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._shutdown_requested = False
        self._reset()

    def _reset(self) -> None:
        self.state = AutomationState.STOPPED
        self.desired_state = "STOPPED"
        self.license_record_id: str | None = None
        self._in_flight: dict[str, Any] | None = None
        self.chat_id: str | None = None
        self.chat_title: str | None = None
        self.chat_ids: list[str] = []
        self.chat_titles: list[str] = []
        self.message: str | None = None
        self.interval_seconds: int | None = None
        self.started_at: datetime | None = None
        self.last_sent_at: datetime | None = None
        self.next_send_at: datetime | None = None
        self.start_at: datetime | None = None
        self.active_weekdays: list[int] = list(range(7))
        self.window_start: str | None = None
        self.window_end: str | None = None
        self.timezone_offset_minutes = 0
        self.max_messages: int | None = None
        self.paused = False
        self.sent_count = self.failed_count = 0
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    def snapshot(self) -> dict[str, Any]:
        iso = lambda value: value.isoformat() if value else None
        return {"state": self.state.value, "running": self.running, "chat_id": self.chat_id,
                "chat_title": self.chat_title, "chat_ids": self.chat_ids,
                "chat_titles": self.chat_titles, "message": self.message,
                "interval_seconds": self.interval_seconds, "started_at": iso(self.started_at),
                "last_sent_at": iso(self.last_sent_at), "next_send_at": iso(self.next_send_at),
                "sent_count": self.sent_count, "failed_count": self.failed_count,
                "last_error": self.last_error, "start_at": iso(self.start_at),
                "active_weekdays": self.active_weekdays, "window_start": self.window_start,
                "window_end": self.window_end, "timezone_offset_minutes": self.timezone_offset_minutes,
                "max_messages": self.max_messages, "paused": self.paused}

    def _persist_job(self) -> None:
        self.runtime.save_job({
            **self.snapshot(),
            "desired_state": self.desired_state,
            "license_record_id": self.license_record_id,
            "in_flight": self._in_flight,
        })

    async def start(self, chat_ids: list[str], message: str, interval_seconds: int, *,
                    license_record_id: str, start_at: datetime | None = None,
                    active_weekdays: list[int] | None = None, window_start: str | None = None,
                    window_end: str | None = None, timezone_offset_minutes: int = 0,
                    max_messages: int | None = None) -> dict[str, Any]:
        async with self._lock:
            if self.running:
                raise RuntimeError("Automation is already running.")
            if interval_seconds < MIN_INTERVAL_SECONDS:
                raise ValueError(f"Interval must be at least {MIN_INTERVAL_SECONDS} seconds.")
            self._shutdown_requested = False
            self.state = AutomationState.STARTING
            dialogs = [await self.telegram.resolve_writable_dialog(chat_id) for chat_id in chat_ids]
            self.chat_ids = list(chat_ids)
            self.chat_titles = [dialog["title"] for dialog in dialogs]
            self.chat_id = self.chat_ids[0]
            self.chat_title = self.chat_titles[0] if len(dialogs) == 1 else f"{len(dialogs)} destinations"
            self.message = message
            self.interval_seconds, self.started_at = interval_seconds, utcnow()
            self.start_at = start_at.astimezone(timezone.utc) if start_at else None
            self.active_weekdays = active_weekdays or list(range(7))
            self.window_start, self.window_end = window_start, window_end
            self.timezone_offset_minutes = timezone_offset_minutes
            self.max_messages, self.paused = max_messages, False
            self.last_sent_at = None
            self.sent_count = self.failed_count = 0
            self.last_error = None
            self.license_record_id = license_record_id
            self.desired_state = "RUNNING"
            self._in_flight = None
            scheduled = self._next_allowed(utcnow())
            self.next_send_at = scheduled
            self.state = AutomationState.WAITING if scheduled > utcnow() else AutomationState.RUNNING
            self._persist_job()
            self._task = asyncio.create_task(self._run(scheduled), name="telegram-automation")
            self._persist_job()
            self.events.add("INFO", "automation_started", f"Automation started for {len(dialogs)} destination(s)", self.chat_title)
            return self.snapshot()

    @staticmethod
    def _parse_time(value: str) -> time:
        hour, minute = (int(part) for part in value.split(":"))
        return time(hour, minute)

    def _next_allowed(self, value: datetime) -> datetime:
        candidate = max(value, self.start_at or value)
        local = candidate - timedelta(minutes=self.timezone_offset_minutes)
        start = self._parse_time(self.window_start) if self.window_start else None
        end = self._parse_time(self.window_end) if self.window_end else None
        for offset in range(8):
            day = local.date() + timedelta(days=offset)
            if day.weekday() not in self.active_weekdays:
                continue
            current = local if offset == 0 else datetime.combine(day, time.min, tzinfo=local.tzinfo)
            if not start or not end:
                allowed = current
            elif start <= end:
                if current.time() > end:
                    continue
                allowed = max(current, datetime.combine(day, start, tzinfo=local.tzinfo))
            else:
                if current.time() <= end or current.time() >= start:
                    allowed = current
                else:
                    allowed = datetime.combine(day, start, tzinfo=local.tzinfo)
            return allowed + timedelta(minutes=self.timezone_offset_minutes)
        return candidate + timedelta(days=7)

    async def _wait_until(self, scheduled: datetime) -> None:
        persisted: tuple[str, str | None] | None = None
        while True:
            if self.paused:
                self.state = AutomationState.PAUSED
                self.next_send_at = None
                marker = (self.state.value, None)
                if marker != persisted:
                    self._persist_job()
                    persisted = marker
                await asyncio.sleep(.5)
                continue
            wait = max(0.0, (scheduled - utcnow()).total_seconds())
            self.next_send_at = scheduled
            self.state = AutomationState.WAITING if wait > 0 else AutomationState.RUNNING
            marker = (self.state.value, scheduled.isoformat())
            if marker != persisted:
                self._persist_job()
                persisted = marker
            if wait <= 0:
                return
            await asyncio.sleep(min(wait, 1.0))

    def _complete(self, message: str) -> None:
        self.state = AutomationState.COMPLETED
        self.desired_state = "COMPLETED"
        self.next_send_at = None
        self._in_flight = None
        self.events.add("INFO", "automation_completed", message, self.chat_title)
        self._persist_job()

    async def _run(self, scheduled: datetime | None = None) -> None:
        scheduled = scheduled or self._next_allowed(utcnow())
        try:
            while True:
                scheduled = self._next_allowed(scheduled)
                await self._wait_until(scheduled)
                try:
                    for chat_id, chat_title in zip(self.chat_ids, self.chat_titles):
                        if self.max_messages is not None and self.sent_count >= self.max_messages:
                            self._complete("Message limit reached")
                            return
                        self.state = AutomationState.RUNNING
                        self._in_flight = {"chat_id": chat_id, "scheduled_for": scheduled.isoformat()}
                        self._persist_job()
                        try:
                            await self._send_with_retry(chat_id, self.message or "")
                            self.last_sent_at = utcnow()
                            self.sent_count += 1
                            self.last_error = None
                            self.events.add("SUCCESS", "message_sent", "Message sent", chat_title)
                            self.runtime.delivery({"timestamp": utcnow().isoformat(), "destination": chat_title, "chat_id": chat_id, "status": "success"})
                        except (errors.FloodWaitError, errors.SlowModeWaitError):
                            raise
                        except Exception as exc:
                            self.failed_count += 1
                            self.last_error = self.telegram.error_message(exc)
                            self.events.add("ERROR", "send_failed", self.last_error, chat_title, self.last_error)
                            self.runtime.delivery({"timestamp": utcnow().isoformat(), "destination": chat_title, "chat_id": chat_id, "status": "failed", "error": self.last_error})
                            if isinstance(exc, errors.MessageTooLongError):
                                self.state = AutomationState.ERROR
                                self.desired_state = "ERROR"
                                self.next_send_at = None
                                self._in_flight = None
                                self._persist_job()
                                return
                        finally:
                            if not self._shutdown_requested and self._in_flight and self._in_flight.get("chat_id") == chat_id:
                                self._in_flight = None
                                self._persist_job()
                        if self.max_messages is not None and self.sent_count >= self.max_messages:
                            self._complete(f"Message limit reached ({self.max_messages})")
                            return
                    scheduled = max(scheduled + timedelta(seconds=self.interval_seconds or 0),
                                    utcnow() + timedelta(seconds=self.interval_seconds or 0))
                    scheduled = self._next_allowed(scheduled)
                    self.next_send_at = scheduled
                    self._persist_job()
                except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
                    self._in_flight = None
                    self.failed_count += 1
                    self.last_error = self.telegram.error_message(exc)
                    self.state = AutomationState.RATE_LIMITED
                    seconds = max(int(getattr(exc, "seconds", 0)), self.interval_seconds or 0)
                    scheduled = utcnow() + timedelta(seconds=seconds)
                    self.next_send_at = scheduled
                    event = "slow_mode" if isinstance(exc, errors.SlowModeWaitError) else "flood_wait"
                    self.events.add("WARNING", event, self.last_error, self.chat_title, self.last_error)
                    self.runtime.delivery({"timestamp": utcnow().isoformat(), "destination": self.chat_title, "status": "rate_limited", "error": self.last_error, "retry_after": seconds})
                    self._persist_job()
                    await asyncio.sleep(seconds)
                    scheduled = utcnow() + timedelta(seconds=self.interval_seconds or MIN_INTERVAL_SECONDS)
                    self.events.add("INFO", "automation_resumed", "Automation resumed", self.chat_title)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = AutomationState.ERROR
            self.desired_state = "ERROR"
            self.last_error = self.telegram.error_message(exc)
            self.events.add("ERROR", "automation_crashed", self.last_error, self.chat_title, self.last_error)
        finally:
            if self._shutdown_requested and self.desired_state == "RUNNING":
                self.state = AutomationState.WAITING
            elif self.desired_state == "STOPPED":
                self.state = AutomationState.STOPPED
            self._in_flight = self._in_flight if self._shutdown_requested else None
            self._persist_job()

    async def _send_with_retry(self, chat_id: str, message: str) -> None:
        for attempt in range(MAX_SEND_RETRIES + 1):
            try:
                await self.telegram.send_message(chat_id, message)
                return
            except (errors.FloodWaitError, errors.SlowModeWaitError):
                raise
            except (ConnectionError, TimeoutError, OSError) as exc:
                if attempt >= MAX_SEND_RETRIES:
                    raise
                delay = RETRY_BACKOFF_SECONDS * (2 ** attempt)
                self.events.add("WARNING", "send_retry", f"Transient send error; retrying in {delay}s", self.chat_title, str(exc))
                await asyncio.sleep(delay)

    async def recover(self, enabled: bool = AUTOMATION_RESUME_ON_START) -> bool:
        if not enabled:
            return False
        async with self._lock:
            if self.running:
                return False
            job = await asyncio.to_thread(self.runtime.job)
            if not isinstance(job, dict):
                return False
            if job.get("desired_state") != "RUNNING" or job.get("state") not in {"RUNNING", "WAITING"}:
                return False
            license_record_id = job.get("license_record_id")
            if not isinstance(license_record_id, str) or not await self.licenses.validate_record(license_record_id):
                self.events.add("WARNING", "automation_recovery_skipped", "Saved automation license is no longer valid")
                self.events.audit("automation_recovery_skipped", reason="invalid_license")
                return False
            telegram_status = await self.telegram.status()
            if not telegram_status.get("authorized"):
                self.events.add("WARNING", "automation_recovery_skipped", "Telegram is not authorized")
                self.events.audit("automation_recovery_skipped", reason="telegram_not_authorized")
                return False
            chat_ids = job.get("chat_ids")
            if not isinstance(chat_ids, list) or not chat_ids or not all(isinstance(value, str) for value in chat_ids):
                return False
            try:
                dialogs = [await self.telegram.resolve_writable_dialog(chat_id) for chat_id in chat_ids]
            except Exception as exc:
                message = self.telegram.error_message(exc)
                self.events.add("WARNING", "automation_recovery_skipped", message, error=message)
                self.events.audit("automation_recovery_skipped", reason="destination_unavailable")
                return False
            interval = job.get("interval_seconds")
            message = job.get("message")
            if not isinstance(interval, int) or interval < MIN_INTERVAL_SECONDS or not isinstance(message, str) or not message.strip():
                return False

            self._restore_fields(job, dialogs, license_record_id)
            now = utcnow()
            saved_next = parse_datetime(job.get("next_send_at"))
            in_flight = job.get("in_flight") if isinstance(job.get("in_flight"), dict) else None
            if in_flight:
                uncertain_at = parse_datetime(in_flight.get("scheduled_for")) or now
                candidate = max(now + timedelta(seconds=interval), uncertain_at + timedelta(seconds=interval))
                self.events.add("WARNING", "automation_recovery_skipped_uncertain",
                                "Skipped an uncertain in-flight delivery to prevent a duplicate", self.chat_title)
                self.events.audit("automation_recovery_skipped_uncertain", scheduled_for=uncertain_at.isoformat())
            elif saved_next and saved_next > now:
                candidate = saved_next
            else:
                candidate = now + timedelta(seconds=interval)
            scheduled = self._next_allowed(candidate)
            self.next_send_at = scheduled
            self.state = AutomationState.WAITING
            self.desired_state = "RUNNING"
            self._in_flight = None
            self._shutdown_requested = False
            self._persist_job()
            self._task = asyncio.create_task(self._run(scheduled), name="telegram-automation")
            self._persist_job()
            self.events.add("INFO", "automation_recovered", "Automation safely resumed after restart", self.chat_title)
            self.events.audit("automation_recovered", next_send_at=scheduled.isoformat(), destination_count=len(self.chat_ids))
            return True

    def _restore_fields(self, job: dict[str, Any], dialogs: list[dict[str, Any]], license_record_id: str) -> None:
        self.chat_ids = list(job["chat_ids"])
        self.chat_titles = [dialog["title"] for dialog in dialogs]
        self.chat_id = self.chat_ids[0]
        self.chat_title = self.chat_titles[0] if len(dialogs) == 1 else f"{len(dialogs)} destinations"
        self.message = job["message"]
        self.interval_seconds = job["interval_seconds"]
        self.started_at = parse_datetime(job.get("started_at")) or utcnow()
        self.last_sent_at = parse_datetime(job.get("last_sent_at"))
        self.start_at = parse_datetime(job.get("start_at"))
        weekdays = job.get("active_weekdays")
        self.active_weekdays = weekdays if isinstance(weekdays, list) and weekdays else list(range(7))
        self.window_start = job.get("window_start") if isinstance(job.get("window_start"), str) else None
        self.window_end = job.get("window_end") if isinstance(job.get("window_end"), str) else None
        self.timezone_offset_minutes = int(job.get("timezone_offset_minutes", 0))
        self.max_messages = job.get("max_messages") if isinstance(job.get("max_messages"), int) else None
        self.sent_count = max(0, int(job.get("sent_count", 0)))
        self.failed_count = max(0, int(job.get("failed_count", 0)))
        self.last_error = None
        self.paused = False
        self.license_record_id = license_record_id

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            self.desired_state = "STOPPED"
            self.paused = False
            self.next_send_at = None
            self._in_flight = None
            if not self.running:
                self.state = AutomationState.STOPPED
                self._persist_job()
                return self.snapshot()
            self.state = AutomationState.STOPPING
            task = self._task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._task = None
            self.state = AutomationState.STOPPED
            self.events.add("INFO", "automation_stopped", "Automation stopped", self.chat_title)
            self._persist_job()
            return self.snapshot()

    async def shutdown(self) -> None:
        """Stop for process shutdown while preserving an eligible desired state."""
        async with self._lock:
            if not self.running:
                return
            self._shutdown_requested = True
            if self.desired_state == "RUNNING":
                self.state = AutomationState.WAITING
                if not self.next_send_at:
                    self.next_send_at = self._next_allowed(utcnow() + timedelta(seconds=self.interval_seconds or MIN_INTERVAL_SECONDS))
            self._persist_job()
            task = self._task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._task = None
            self._persist_job()

    async def pause(self) -> dict[str, Any]:
        async with self._lock:
            if not self.running:
                raise RuntimeError("No running automation to pause.")
            if not self.paused:
                self.paused = True
                self.desired_state = "PAUSED"
                self.state = AutomationState.PAUSED
                self.next_send_at = None
                self.events.add("INFO", "automation_paused", "Automation paused", self.chat_title)
                self._persist_job()
            return self.snapshot()

    async def resume(self) -> dict[str, Any]:
        async with self._lock:
            if not self.running:
                raise RuntimeError("No paused automation to resume.")
            if self.paused:
                self.paused = False
                self.desired_state = "RUNNING"
                self.state = AutomationState.WAITING
                self.events.add("INFO", "automation_resumed", "Automation resumed", self.chat_title)
                self._persist_job()
            return self.snapshot()
