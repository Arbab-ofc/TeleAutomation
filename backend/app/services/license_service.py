import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any

from app.config import ADMIN_ACCESS_CODE, ADMIN_TELEGRAM_USERNAME, CONTACT_FILE, FIREBASE_DATABASE_URL, FIREBASE_SERVICE_ACCOUNT_JSON, LICENSE_TOKEN_SECRET

try:
    import firebase_admin
    from firebase_admin import credentials, db
except ImportError:  # Firebase is installed in production via requirements.txt.
    firebase_admin = None
    credentials = db = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LicenseService:
    """Firebase-backed license keys with a local development fallback."""

    def __init__(self) -> None:
        self._local: dict[str, dict[str, Any]] = {}
        self._firebase = False
        if firebase_admin and FIREBASE_DATABASE_URL and FIREBASE_SERVICE_ACCOUNT_JSON:
            raw = FIREBASE_SERVICE_ACCOUNT_JSON
            info = json.loads(raw) if raw.startswith("{") else raw
            cred = credentials.Certificate(info)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})
            self._firebase = True

    @property
    def configured(self) -> bool:
        return bool(self._firebase or (ADMIN_ACCESS_CODE and LICENSE_TOKEN_SECRET))

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def _ref(self):
        return db.reference("license_keys")

    def _read(self) -> dict[str, dict[str, Any]]:
        raw = (self._ref().get() or {}) if self._firebase else self._local
        if not isinstance(raw, dict):
            return {}
        # Normalize older deployments where one license record was written directly
        # under license_keys instead of under a generated record id.
        if isinstance(raw.get("key_hash"), str) and "expires_at" in raw:
            return {"legacy": raw}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict) and "key_hash" in value}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        if self._firebase:
            self._ref().set(data)
        else:
            self._local = data

    def _cleanup_sync(self) -> None:
        records = self._read()
        now = _now().timestamp()
        expired = [item_id for item_id, item in records.items() if float(item.get("expires_at", 0)) <= now]
        if expired:
            if self._firebase:
                for item_id in expired:
                    self._ref().child(item_id).delete()
            else:
                for item_id in expired:
                    records.pop(item_id, None)
                self._write(records)

    async def cleanup(self) -> None:
        await asyncio.to_thread(self._cleanup_sync)

    def _generate_sync(self, duration_value: int | None, duration_unit: str | None, start_date: date | None = None, end_date: date | None = None) -> dict[str, Any]:
        self._cleanup_sync()
        records = self._read()
        existing = {str(item.get("key_hash")) for item in records.values()}
        while True:
            key = "".join(secrets.choice("0123456789") for _ in range(12))
            if self._hash(key) not in existing:
                break
        created = _now()
        starts = datetime.combine(start_date, dt_time.min, tzinfo=timezone.utc) if start_date else created
        expires = (datetime.combine(end_date + timedelta(days=1), dt_time.min, tzinfo=timezone.utc) - timedelta(microseconds=1)) if end_date else starts + timedelta(**{duration_unit: duration_value})
        record = {"key_hash": self._hash(key), "created_at": created.timestamp(), "starts_at": starts.timestamp(), "expires_at": expires.timestamp(), "duration_value": duration_value, "duration_unit": duration_unit, "start_date": start_date.isoformat() if start_date else None, "end_date": end_date.isoformat() if end_date else None, "used": False, "status": "active", "revoked_at": None}
        if self._firebase:
            item_id = self._ref().push().key
        else:
            item_id = secrets.token_hex(12)
        record_id = item_id or secrets.token_hex(12)
        if self._firebase:
            # push() gives a collision-safe id; set() atomically replaces only this
            # child and avoids transaction callbacks being skipped by old data.
            self._ref().child(record_id).set(record)
            if not self._ref().child(record_id).get():
                raise RuntimeError("Firebase did not persist the generated license.")
        else:
            records[record_id] = record
            self._write(records)
        return {"id": record_id, "key": key, "created_at": created.isoformat(), "starts_at": starts.isoformat(), "expires_at": expires.isoformat(), "duration_value": duration_value, "duration_unit": duration_unit, "start_date": start_date.isoformat() if start_date else None, "end_date": end_date.isoformat() if end_date else None}

    async def generate(self, duration_value: int | None, duration_unit: str | None, start_date: date | None = None, end_date: date | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._generate_sync, duration_value, duration_unit, start_date, end_date)

    def _list_sync(self) -> list[dict[str, Any]]:
        self._cleanup_sync()
        now = _now().timestamp()
        result = []
        for item_id, item in self._read().items():
            expires = float(item.get("expires_at", 0))
            starts = float(item.get("starts_at", item.get("created_at", 0)))
            status = str(item.get("status", "active"))
            if status != "revoked" and now < starts:
                status = "scheduled"
            elif status != "revoked" and now >= expires:
                status = "expired"
            result.append({"id": item_id, "key_hint": str(item.get("key_hash", ""))[-6:], "starts_at": datetime.fromtimestamp(starts, timezone.utc).isoformat(), "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(), "active": status == "active" and starts <= now < expires, "status": status, "revoked_at": item.get("revoked_at"), "duration_value": item.get("duration_value"), "duration_unit": item.get("duration_unit"), "start_date": item.get("start_date"), "end_date": item.get("end_date")})
        return sorted(result, key=lambda item: item["expires_at"])

    async def list(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_sync)

    def _revoke_sync(self, record_id: str) -> bool:
        records = self._read()
        if record_id not in records:
            return False
        if self._firebase:
            self._ref().child(record_id).delete()
        else:
            records.pop(record_id, None)
            self._write(records)
        return True

    async def revoke(self, record_id: str) -> bool:
        return await asyncio.to_thread(self._revoke_sync, record_id)

    def _validate_sync(self, key: str, include_id: bool = False) -> dict[str, Any] | None:
        self._cleanup_sync()
        digest = self._hash(key)
        now = _now().timestamp()
        for item_id, item in self._read().items():
            starts_at = float(item.get("starts_at", item.get("created_at", 0)))
            if str(item.get("status", "active")) != "revoked" and hmac.compare_digest(str(item.get("key_hash", "")), digest) and starts_at <= now < float(item.get("expires_at", 0)):
                result = {"valid": True, "starts_at": datetime.fromtimestamp(starts_at, timezone.utc).isoformat(), "expires_at": datetime.fromtimestamp(float(item["expires_at"]), timezone.utc).isoformat()}
                if include_id:
                    result["id"] = item_id
                return result
        return None

    async def validate(self, key: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._validate_sync, key)

    async def validate_for_job(self, key: str) -> dict[str, Any] | None:
        """Validate a raw key once and return its internal record id for safe recovery."""
        return await asyncio.to_thread(self._validate_sync, key, True)

    def _validate_record_sync(self, record_id: str) -> bool:
        self._cleanup_sync()
        item = self._read().get(record_id)
        if not item:
            return False
        now = _now().timestamp()
        starts_at = float(item.get("starts_at", item.get("created_at", 0)))
        expires_at = float(item.get("expires_at", 0))
        return str(item.get("status", "active")) != "revoked" and starts_at <= now < expires_at

    async def validate_record(self, record_id: str) -> bool:
        return await asyncio.to_thread(self._validate_record_sync, record_id)

    def admin_token(self) -> str:
        expires = int(time.time()) + 3600
        payload = f"admin:{expires}"
        signature = hmac.new(LICENSE_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}:{signature}"

    def verify_admin_token(self, token: str) -> bool:
        try:
            role, raw_expiry, signature = token.split(":", 2)
            payload = f"{role}:{raw_expiry}"
            return role == "admin" and int(raw_expiry) > int(time.time()) and hmac.compare_digest(signature, hmac.new(LICENSE_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest())
        except (ValueError, TypeError):
            return False

    def verify_admin_code(self, code: str) -> str | None:
        if not ADMIN_ACCESS_CODE or not LICENSE_TOKEN_SECRET or not hmac.compare_digest(code, ADMIN_ACCESS_CODE):
            return None
        return self.admin_token()

    def _contact_ref(self):
        return db.reference("app_config/admin_contact")

    def get_admin_contact(self) -> str:
        if self._firebase:
            value = self._contact_ref().get()
            return str(value or ADMIN_TELEGRAM_USERNAME).lstrip("@")
        try:
            data = json.loads(CONTACT_FILE.read_text(encoding="utf-8"))
            return str(data.get("telegram_username") or ADMIN_TELEGRAM_USERNAME).lstrip("@")
        except (OSError, json.JSONDecodeError, AttributeError):
            return ADMIN_TELEGRAM_USERNAME

    def set_admin_contact(self, username: str) -> str:
        clean = username.strip().lstrip("@")
        if self._firebase:
            self._contact_ref().set(clean)
        else:
            CONTACT_FILE.write_text(json.dumps({"telegram_username": clean}, indent=2), encoding="utf-8")
        return clean
