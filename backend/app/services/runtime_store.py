import json
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from app.config import DELIVERY_HISTORY_FILE, JOB_FILE

try:
    import firebase_admin
    from firebase_admin import db
except ImportError:
    firebase_admin = None
    db = None


class RuntimeStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._firebase = bool(firebase_admin and firebase_admin._apps and db)

    @property
    def backend(self) -> str:
        return "firebase" if self._firebase else "local"

    def _ref(self):
        return db.reference("runtime")

    def save_job(self, snapshot: dict[str, Any]) -> None:
        payload = {**snapshot, "updated_at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            if self._firebase:
                self._ref().child("job").set(payload)
            else:
                JOB_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def job(self) -> dict[str, Any] | None:
        if self._firebase:
            return self._ref().child("job").get()
        try:
            return json.loads(JOB_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def delivery(self, item: dict[str, Any]) -> None:
        with self._lock:
            if self._firebase:
                self._ref().child("deliveries").push().set(item)
            else:
                with DELIVERY_HISTORY_FILE.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(item, ensure_ascii=False) + "\n")

    def deliveries(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._firebase:
            rows = self._ref().child("deliveries").get() or {}
            return sorted(rows.values(), key=lambda row: row.get("timestamp", ""), reverse=True)[:max(1, min(limit, 500))]
        try:
            rows = [json.loads(line) for line in DELIVERY_HISTORY_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
            return rows[-max(1, min(limit, 500)):][::-1]
        except (OSError, json.JSONDecodeError):
            return []

    @staticmethod
    def _retained_items(rows: list[tuple[str | None, dict[str, Any]]], days: int,
                        max_records: int) -> tuple[list[tuple[str | None, dict[str, Any]]], list[str]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))

        def parsed_timestamp(item: dict[str, Any]) -> datetime:
            try:
                value = datetime.fromisoformat(str(item.get("timestamp", "")))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc)
            except (TypeError, ValueError):
                return datetime.min.replace(tzinfo=timezone.utc)

        ordered = sorted(rows, key=lambda row: parsed_timestamp(row[1]), reverse=True)
        retained = [row for row in ordered if parsed_timestamp(row[1]) >= cutoff][:max(1, max_records)]
        retained_keys = {key for key, _ in retained if key is not None}
        removed_keys = [key for key, _ in rows if key is not None and key not in retained_keys]
        return retained, removed_keys

    def cleanup_deliveries(self, days: int, max_records: int) -> int:
        """Apply opt-in age/count retention and return the number of deleted records."""
        with self._lock:
            if self._firebase:
                raw = self._ref().child("deliveries").get() or {}
                if not isinstance(raw, dict):
                    return 0
                rows = [(str(key), value) for key, value in raw.items() if isinstance(value, dict)]
                _, removed_keys = self._retained_items(rows, days, max_records)
                for key in removed_keys:
                    self._ref().child("deliveries").child(key).delete()
                return len(removed_keys)
            try:
                rows = [(None, json.loads(line)) for line in DELIVERY_HISTORY_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
            except (OSError, json.JSONDecodeError):
                return 0
            retained, _ = self._retained_items(rows, days, max_records)
            if len(retained) == len(rows):
                return 0
            ordered = list(reversed([item for _, item in retained]))
            temp = DELIVERY_HISTORY_FILE.with_suffix(DELIVERY_HISTORY_FILE.suffix + ".tmp")
            temp.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in ordered), encoding="utf-8")
            temp.replace(DELIVERY_HISTORY_FILE)
            return len(rows) - len(retained)
