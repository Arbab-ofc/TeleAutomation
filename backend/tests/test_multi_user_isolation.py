import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch
from uuid import UUID, uuid4

from app.main import health
from app.services.app_session_service import AppSessionService
from app.services.automation_service import AutomationService
from app.services.runtime_store import RuntimeStore
from app.services.license_service import LicenseService
from app.services.storage_service import StorageService
from app.services.telegram_service import TelegramService
from app.services.telegram_manager import TelegramClientManager


class MemoryRuntime:
    def __init__(self):
        self.jobs = {}
        self.rows = {}

    def save_job(self, user_id, snapshot):
        self.jobs[user_id] = dict(snapshot)

    def job(self, user_id):
        return self.jobs.get(user_id)

    def delivery(self, user_id, item):
        self.rows.setdefault(user_id, []).append(dict(item))


class FakeTelegram:
    def __init__(self, user_id):
        self.user_id = user_id
        self.sent = []
        self.logged_out = False

    async def status(self):
        return {"authorized": True, "user": self.user_id}

    async def resolve_writable_dialog(self, chat_id):
        if not chat_id.startswith(self.user_id):
            raise LookupError("foreign destination")
        return {"id": chat_id, "title": chat_id, "can_send": True}

    async def dialogs(self):
        return [{"id": f"{self.user_id}-group", "title": self.user_id, "can_send": True}]

    async def send_message(self, chat_id, message):
        self.sent.append((chat_id, message))

    async def logout(self):
        self.logged_out = True

    @staticmethod
    def error_message(exc):
        return str(exc)


class FakeEvents:
    def __init__(self):
        self.entries = []

    def add(self, level, event, message, destination=None, error=None):
        self.entries.append({"level": level, "event": event})

    def audit(self, action, actor="admin", **details):
        return None


class FakeLicenses:
    async def validate_record(self, record_id, user_id=None):
        return True


class ApplicationSessionIsolationTests(unittest.TestCase):
    def test_two_browser_tokens_map_to_distinct_stable_users(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            AppSessionService, "_firebase", new_callable=PropertyMock, return_value=False
        ):
            service = AppSessionService(Path(directory) / "sessions.json")
            user_a, token_a = service.create()
            user_b, token_b = service.create()
            self.assertNotEqual(user_a.id, user_b.id)
            self.assertEqual(user_a, service.resolve(token_a))
            self.assertEqual(user_b, service.resolve(token_b))
            self.assertIsNone(service.resolve("forged-token"))

    def test_logout_invalidates_only_the_correct_browser(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            AppSessionService, "_firebase", new_callable=PropertyMock, return_value=False
        ):
            service = AppSessionService(Path(directory) / "sessions.json")
            user_a, token_a = service.create()
            user_b, token_b = service.create()
            service.invalidate(token_a)
            self.assertIsNone(service.resolve(token_a))
            self.assertEqual(user_b, service.resolve(token_b))

    def test_rotated_session_keeps_owner_and_rejects_old_token(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            AppSessionService, "_firebase", new_callable=PropertyMock, return_value=False
        ):
            service = AppSessionService(Path(directory) / "sessions.json")
            user, token = service.create()
            replacement = service.rotate(token, user.id)
            self.assertIsNone(service.resolve(token))
            self.assertEqual(user, service.resolve(replacement))


class TelegramIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_reuses_only_the_same_users_client(self):
        class ManagedTelegram:
            def __init__(self, storage, user_id):
                self.user_id = user_id
                self.authorized = user_id == "user-a"

            async def initialize(self):
                return None

            async def disconnect(self):
                return None

        manager = TelegramClientManager(SimpleNamespace())
        with patch("app.services.telegram_manager.TelegramService", ManagedTelegram):
            a, a_again = await asyncio.gather(manager.get("user-a"), manager.get("user-a"))
            b = await manager.get("user-b")
        self.assertIs(a, a_again)
        self.assertIsNot(a, b)
        self.assertTrue(a.authorized)
        self.assertFalse(b.authorized)

    async def test_session_paths_and_pending_auth_are_user_scoped(self):
        storage = SimpleNamespace()
        user_a, user_b = str(uuid4()), str(uuid4())
        a = TelegramService(storage, user_a)
        b = TelegramService(storage, user_b)
        self.assertEqual(UUID(user_a), UUID(a.session_dir.name))
        self.assertNotEqual(a.session_file, b.session_file)
        a._phone = "+10000000001"
        a._phone_code_hash = "hash-a"
        a._pending_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        b.client = SimpleNamespace()
        with self.assertRaisesRegex(RuntimeError, "Request a verification code"):
            await b.verify_code("12345")
        self.assertTrue(a.pending_login)
        self.assertFalse(b.pending_login)

    async def test_groups_and_logout_do_not_cross_users(self):
        a, b = FakeTelegram("a"), FakeTelegram("b")
        self.assertEqual("a-group", (await a.dialogs())[0]["id"])
        self.assertEqual("b-group", (await b.dialogs())[0]["id"])
        await a.logout()
        self.assertTrue(a.logged_out)
        self.assertFalse(b.logged_out)


class UserDataIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_messages_are_scoped_and_foreign_ids_cannot_modify(self):
        with tempfile.TemporaryDirectory() as directory:
            service = StorageService()
            root = Path(directory)
            with patch.object(StorageService, "_firebase", new_callable=PropertyMock, return_value=False), patch.object(service, "_user_file", side_effect=lambda user_id: root / user_id / "messages.json"):
                item = await service.create_message("user-a", "A", "secret")
                self.assertEqual([], await service.list_messages("user-b"))
                with self.assertRaises(KeyError):
                    await service.update_message("user-b", item["id"], "stolen", "x")
                self.assertEqual("secret", (await service.list_messages("user-a"))[0]["content"])

    async def test_automation_state_start_stop_and_counters_are_independent(self):
        runtime = MemoryRuntime()
        a_tg, b_tg = FakeTelegram("a"), FakeTelegram("b")
        a = AutomationService(a_tg, FakeEvents(), runtime, FakeLicenses(), "user-a")
        b = AutomationService(b_tg, FakeEvents(), runtime, FakeLicenses(), "user-b")
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        await a.start(["a-group"], "A", 60, license_record_id="license-a", start_at=future)
        self.assertTrue(a.running)
        self.assertFalse(b.running)
        b.sent_count = 7
        a.sent_count = 2
        await a.stop()
        self.assertFalse(a.running)
        self.assertEqual(7, b.sent_count)
        self.assertIn("user-a", runtime.jobs)
        self.assertNotIn("user-b", runtime.jobs)

    async def test_recovery_disabled_does_not_resume_any_user(self):
        runtime = MemoryRuntime()
        runtime.jobs["user-a"] = {"desired_state": "RUNNING", "state": "WAITING"}
        service = AutomationService(FakeTelegram("a"), FakeEvents(), runtime, FakeLicenses(), "user-a")
        self.assertFalse(await service.recover(enabled=False))
        self.assertFalse(service.running)


class FirebasePathAndHealthTests(unittest.TestCase):
    def test_runtime_firebase_root_is_user_scoped(self):
        calls = []

        class Ref:
            def child(self, value):
                calls.append(value)
                return self

        fake_db = SimpleNamespace(reference=lambda value: calls.append(value) or Ref())
        store = RuntimeStore()
        store._firebase = True
        with patch("app.services.runtime_store.db", fake_db):
            store._ref("opaque-user")
        self.assertEqual(["users", "opaque-user", "runtime"], calls)

    def test_message_firebase_root_is_user_scoped(self):
        calls = []

        class Ref:
            def child(self, value):
                calls.append(value)
                return self

        fake_db = SimpleNamespace(reference=lambda value: calls.append(value) or Ref())
        service = StorageService()
        with patch("app.services.storage_service.db", fake_db):
            service._messages_ref("opaque-user")
        self.assertEqual(["users", "opaque-user", "messages"], calls)

    def test_health_contains_no_user_telegram_or_automation_state(self):
        class Settings:
            async def get_settings(self):
                return {"api_id": "1"}

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            storage=Settings(), licenses=SimpleNamespace(_firebase=False),
            runtime=SimpleNamespace(backend="local"), started_at=0,
        )))
        result = asyncio.run(health(request))
        self.assertTrue(result["telegram_configured"])
        self.assertNotIn("telegram", result)
        self.assertNotIn("automation", result)


class LicenseDisplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_license_is_encrypted_at_rest_and_admin_can_list_real_key(self):
        service = LicenseService.__new__(LicenseService)
        service._local = {}
        service._firebase = False
        service._claim_lock = Lock()
        with patch("app.services.license_service.LICENSE_TOKEN_SECRET", "test-secret-with-enough-entropy"):
            created = await service.generate(1, "days")
            stored = next(iter(service._local.values()))
            self.assertNotIn(created["key"], str(stored))
            self.assertNotEqual(created["key"], stored["key_encrypted"])
            listed = await service.list()
        self.assertEqual(created["key"], listed[0]["key"])
        self.assertEqual(created["key"][-6:], listed[0]["key_hint"])

    async def test_legacy_hash_only_license_is_reported_as_unavailable(self):
        service = LicenseService.__new__(LicenseService)
        service._firebase = False
        service._claim_lock = Lock()
        now = datetime.now(timezone.utc).timestamp()
        service._local = {"legacy": {"key_hash": "a" * 64, "created_at": now,
                                      "starts_at": now, "expires_at": now + 3600}}
        with patch("app.services.license_service.LICENSE_TOKEN_SECRET", "test-secret"):
            listed = await service.list()
        self.assertIsNone(listed[0]["key"])
        self.assertIsNone(listed[0]["key_hint"])


if __name__ == "__main__":
    unittest.main()
