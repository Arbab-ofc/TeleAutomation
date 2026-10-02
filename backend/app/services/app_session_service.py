import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from app.config import APP_SESSIONS_FILE, APP_SESSION_TTL_SECONDS

try:
    import firebase_admin
    from firebase_admin import db
except ImportError:
    firebase_admin = None
    db = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AppUser:
    id: str


class AppSessionService:
    """Persistent, opaque browser sessions. Only SHA-256 token digests are stored."""

    def __init__(self, path: Path = APP_SESSIONS_FILE) -> None:
        self.path = path
        self._lock = Lock()

    @property
    def _firebase(self) -> bool:
        return bool(firebase_admin and firebase_admin._apps and db)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _read_local(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_local(self, rows: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)

    def _get(self, digest: str) -> dict[str, Any] | None:
        if self._firebase:
            value = db.reference("app_sessions").child(digest).get()
            return value if isinstance(value, dict) else None
        return self._read_local().get(digest)

    def _set(self, digest: str, record: dict[str, Any]) -> None:
        if self._firebase:
            db.reference("app_sessions").child(digest).set(record)
        else:
            rows = self._read_local()
            rows[digest] = record
            self._write_local(rows)

    def _delete(self, digest: str) -> None:
        if self._firebase:
            db.reference("app_sessions").child(digest).delete()
        else:
            rows = self._read_local()
            rows.pop(digest, None)
            self._write_local(rows)

    def resolve(self, token: str | None) -> AppUser | None:
        if not token:
            return None
        with self._lock:
            record = self._get(self._digest(token))
            if not record:
                return None
            try:
                expires_at = datetime.fromisoformat(str(record["expires_at"]))
            except (KeyError, TypeError, ValueError):
                return None
            if expires_at <= _now():
                self._delete(self._digest(token))
                return None
            user_id = str(record.get("user_id", ""))
            try:
                # Reject malformed or path-like identifiers even if storage is corrupted.
                if str(UUID(user_id)) != user_id:
                    return None
            except ValueError:
                return None
            return AppUser(user_id)

    def create(self, user_id: str | None = None) -> tuple[AppUser, str]:
        with self._lock:
            user = AppUser(user_id or str(uuid4()))
            token = secrets.token_urlsafe(48)
            now = _now()
            self._set(self._digest(token), {
                "user_id": user.id,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=APP_SESSION_TTL_SECONDS)).isoformat(),
            })
            return user, token

    def rotate(self, token: str, user_id: str) -> str:
        with self._lock:
            self._delete(self._digest(token))
            replacement = secrets.token_urlsafe(48)
            now = _now()
            self._set(self._digest(replacement), {
                "user_id": user_id,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=APP_SESSION_TTL_SECONDS)).isoformat(),
            })
            return replacement

    def invalidate(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._delete(self._digest(token))
