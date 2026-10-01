import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default

DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = BASE_DIR / "sessions"
SETTINGS_FILE = DATA_DIR / "settings.json"
MESSAGES_FILE = DATA_DIR / "messages.json"
LOG_FILE = DATA_DIR / "automation.log"
AUDIT_LOG_FILE = DATA_DIR / "audit.log"
DELIVERY_HISTORY_FILE = DATA_DIR / "delivery_history.jsonl"
JOB_FILE = DATA_DIR / "automation_job.json"
CONTACT_FILE = DATA_DIR / "contact.json"
SESSION_FILE = SESSIONS_DIR / "telegram_user"
ADMIN_ACCESS_CODE = os.getenv("ADMIN_ACCESS_CODE", "").strip()
ADMIN_TELEGRAM_USERNAME = os.getenv("ADMIN_TELEGRAM_USERNAME", "").strip().lstrip("@")
LICENSE_TOKEN_SECRET = os.getenv("LICENSE_TOKEN_SECRET", "").strip()
FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL", "").strip()
FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"
COOKIE_SECURE = env_bool("COOKIE_SECURE", IS_PRODUCTION)
AUTOMATION_RESUME_ON_START = env_bool("AUTOMATION_RESUME_ON_START", False)
DELIVERY_RETENTION_ENABLED = env_bool("DELIVERY_RETENTION_ENABLED", False)
DELIVERY_RETENTION_DAYS = env_int("DELIVERY_RETENTION_DAYS", 90)
DELIVERY_RETENTION_MAX_RECORDS = env_int("DELIVERY_RETENTION_MAX_RECORDS", 10000)
DELIVERY_RETENTION_INTERVAL_SECONDS = env_int("DELIVERY_RETENTION_INTERVAL_SECONDS", 3600, 60)
MIN_INTERVAL_SECONDS = 10
MAX_LOG_ENTRIES = 200
MAX_SEND_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
_configured_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]
_development_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
CORS_ORIGINS = _configured_origins if IS_PRODUCTION else list(dict.fromkeys(_configured_origins + _development_origins))

for directory in (DATA_DIR, SESSIONS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
