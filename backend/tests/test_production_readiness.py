import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.services.automation_service import AutomationService
from app.services.runtime_store import RuntimeStore


def now() -> datetime:
    return datetime.now(timezone.utc)


class FakeRuntime:
    def __init__(self, job=None):
        self.saved_job = job
        self.saved_jobs = []
        self.delivery_items = []

    def save_job(self, user_id, snapshot):
        self.saved_job = dict(snapshot)
        self.saved_jobs.append(dict(snapshot))

    def job(self, user_id):
        return self.saved_job

    def delivery(self, user_id, item):
        self.delivery_items.append(dict(item))


class FakeEvents:
    def __init__(self):
        self.entries = []
        self.audits = []

    def add(self, level, event, message, destination=None, error=None):
        self.entries.append({"level": level, "event": event, "message": message,
                             "destination": destination, "error": error})

    def audit(self, action, actor="admin", **details):
        self.audits.append({"action": action, "actor": actor, **details})


class FakeTelegram:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.sent = []

    async def status(self):
        return {"authorized": self.authorized}

    async def resolve_writable_dialog(self, chat_id):
        return {"id": chat_id, "title": f"Destination {chat_id}", "can_send": True}

    async def send_message(self, chat_id, message):
        self.sent.append((chat_id, message))

    @staticmethod
    def error_message(exc):
        return str(exc)


class FakeLicenses:
    def __init__(self, valid=True):
        self.valid = valid

    async def validate_record(self, record_id, user_id=None):
        return self.valid and record_id == "license-record"


def resumable_job(**overrides):
    job = {
        "state": "WAITING",
        "desired_state": "RUNNING",
        "license_record_id": "license-record",
        "chat_ids": ["-1001"],
        "chat_titles": ["Old title"],
        "message": "Recovery test",
        "interval_seconds": 60,
        "started_at": (now() - timedelta(minutes=5)).isoformat(),
        "last_sent_at": None,
        "next_send_at": (now() + timedelta(minutes=5)).isoformat(),
        "start_at": None,
        "active_weekdays": list(range(7)),
        "window_start": None,
        "window_end": None,
        "timezone_offset_minutes": 0,
        "max_messages": None,
        "sent_count": 0,
        "failed_count": 0,
        "paused": False,
        "in_flight": None,
    }
    job.update(overrides)
    return job


class AutomationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_is_disabled_by_default_argument_override(self):
        runtime = FakeRuntime(resumable_job())
        service = AutomationService(FakeTelegram(), FakeEvents(), runtime, FakeLicenses())

        self.assertFalse(await service.recover(enabled=False))
        self.assertFalse(service.running)
        self.assertEqual([], service.telegram.sent)

    async def test_only_running_or_waiting_jobs_resume(self):
        for state in ("STOPPED", "PAUSED", "COMPLETED", "ERROR", "RATE_LIMITED", "STARTING"):
            with self.subTest(state=state):
                runtime = FakeRuntime(resumable_job(state=state))
                service = AutomationService(FakeTelegram(), FakeEvents(), runtime, FakeLicenses())
                self.assertFalse(await service.recover(enabled=True))
                self.assertFalse(service.running)

    async def test_recovery_revalidates_license_telegram_and_destination(self):
        runtime = FakeRuntime(resumable_job())
        invalid_license = AutomationService(FakeTelegram(), FakeEvents(), runtime, FakeLicenses(False))
        self.assertFalse(await invalid_license.recover(enabled=True))

        unauthorized = AutomationService(FakeTelegram(False), FakeEvents(), runtime, FakeLicenses())
        self.assertFalse(await unauthorized.recover(enabled=True))

    async def test_recovery_schedules_future_send_without_immediate_delivery(self):
        runtime = FakeRuntime(resumable_job())
        telegram, events = FakeTelegram(), FakeEvents()
        service = AutomationService(telegram, events, runtime, FakeLicenses())

        self.assertTrue(await service.recover(enabled=True))
        await asyncio.sleep(0)
        self.assertTrue(service.running)
        self.assertEqual([], telegram.sent)
        self.assertGreater(service.next_send_at, now())
        self.assertIn("automation_recovered", [entry["event"] for entry in events.entries])
        self.assertNotIn("license_key", runtime.saved_job)
        await service.shutdown()

    async def test_uncertain_in_flight_send_is_skipped(self):
        scheduled = now() - timedelta(seconds=5)
        runtime = FakeRuntime(resumable_job(
            state="RUNNING",
            next_send_at=scheduled.isoformat(),
            in_flight={"chat_id": "-1001", "scheduled_for": scheduled.isoformat()},
        ))
        telegram, events = FakeTelegram(), FakeEvents()
        service = AutomationService(telegram, events, runtime, FakeLicenses())

        before = now()
        self.assertTrue(await service.recover(enabled=True))
        await asyncio.sleep(0)
        self.assertEqual([], telegram.sent)
        self.assertGreaterEqual(service.next_send_at, before + timedelta(seconds=59))
        self.assertIn("automation_recovery_skipped_uncertain", [entry["event"] for entry in events.entries])
        await service.shutdown()

    async def test_start_persists_record_id_not_raw_license(self):
        runtime = FakeRuntime()
        service = AutomationService(FakeTelegram(), FakeEvents(), runtime, FakeLicenses())

        await service.start(
            ["-1001"], "Scheduled message", 60,
            license_record_id="license-record",
            start_at=now() + timedelta(minutes=10),
        )
        self.assertEqual("license-record", runtime.saved_job["license_record_id"])
        self.assertNotIn("license_key", runtime.saved_job)
        await service.shutdown()


class DeliveryRetentionTests(unittest.TestCase):
    def test_local_retention_keeps_recent_records_and_count_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "deliveries.jsonl"
            rows = [
                {"timestamp": (now() - timedelta(days=100)).isoformat(), "id": "old"},
                {"timestamp": (now() - timedelta(days=3)).isoformat(), "id": "recent-1"},
                {"timestamp": (now() - timedelta(days=2)).isoformat(), "id": "recent-2"},
                {"timestamp": (now() - timedelta(days=1)).isoformat(), "id": "recent-3"},
            ]
            history.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            store = RuntimeStore()
            store._firebase = False

            with patch.object(store, "_delivery_file", return_value=history):
                removed = store.cleanup_deliveries("test-user", days=30, max_records=2)

            retained = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(2, removed)
            self.assertEqual(["recent-2", "recent-3"], [row["id"] for row in retained])


if __name__ == "__main__":
    unittest.main()
