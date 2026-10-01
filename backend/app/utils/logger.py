import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.config import AUDIT_LOG_FILE, LOG_FILE, MAX_LOG_ENTRIES

logger = logging.getLogger("automation")


class EventLog:
    def __init__(self) -> None:
        self.entries: deque[dict[str, Any]] = deque(maxlen=MAX_LOG_ENTRIES)

    def add(self, level: str, event: str, message: str, destination: str | None = None,
            error: str | None = None) -> dict[str, Any]:
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": level, "event": event,
                 "message": message, "destination": destination, "error": error}
        self.entries.append(entry)
        try:
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Could not append automation log: %s", exc)
        return entry

    def latest(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.entries)[-max(1, min(limit, MAX_LOG_ENTRIES)):]

    def audit(self, action: str, actor: str = "admin", **details: Any) -> dict[str, Any]:
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "action": action, "actor": actor, **details}
        try:
            with AUDIT_LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Could not append audit log: %s", exc)
        return entry

    def audit_latest(self, limit: int = 100) -> list[dict[str, Any]]:
        if not AUDIT_LOG_FILE.exists():
            return []
        try:
            rows = [json.loads(line) for line in AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
            return rows[-max(1, min(limit, MAX_LOG_ENTRIES)):]
        except (OSError, json.JSONDecodeError):
            return []
