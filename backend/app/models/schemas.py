from datetime import date, datetime
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class SettingsUpdate(ApiModel):
    api_id: str
    api_hash: str

    @field_validator("api_id")
    @classmethod
    def numeric_api_id(cls, value: str) -> str:
        if not value.isdigit() or int(value) <= 0:
            raise ValueError("Telegram API ID must be a positive number.")
        return value

    @field_validator("api_hash")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        if not value:
            raise ValueError("Telegram API Hash is required.")
        return value


class PhoneRequest(ApiModel):
    phone: str = Field(min_length=7, max_length=20, pattern=r"^\+[1-9]\d{6,14}$")


class CodeRequest(ApiModel):
    code: str = Field(min_length=2, max_length=10, pattern=r"^\d+$")


class PasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class MessagePayload(ApiModel):
    chat_ids: list[str] = Field(min_length=1, max_length=50)
    message: str

    @field_validator("chat_ids")
    @classmethod
    def unique_destinations(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not cleaned:
            raise ValueError("Select at least one destination.")
        return cleaned

    @field_validator("message")
    @classmethod
    def nonempty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be empty.")
        return value


class AutomationStart(MessagePayload):
    interval_seconds: int
    license_key: str = Field(min_length=12, max_length=12, pattern=r"^\d{12}$")
    start_at: datetime | None = None
    active_weekdays: list[int] = Field(default_factory=lambda: list(range(7)))
    window_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    window_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone_offset_minutes: int = Field(default=0, ge=-840, le=840)
    max_messages: int | None = Field(default=None, ge=1, le=100000)

    @field_validator("active_weekdays")
    @classmethod
    def valid_weekdays(cls, value: list[int]) -> list[int]:
        cleaned = sorted(set(value))
        if not cleaned or any(day < 0 or day > 6 for day in cleaned):
            raise ValueError("Select at least one valid delivery day.")
        return cleaned


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=20000)

    @field_validator("name", "content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value cannot be empty.")
        return value.strip() if len(value) <= 100 else value


class TemplateUpdate(TemplateCreate):
    pass


class AutomationState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    RATE_LIMITED = "RATE_LIMITED"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    ERROR = "ERROR"
    COMPLETED = "COMPLETED"


class LogEntry(BaseModel):
    timestamp: datetime
    level: Literal["INFO", "SUCCESS", "WARNING", "ERROR"]
    event: str
    message: str
    destination: str | None = None
    error: str | None = None


class AdminVerify(ApiModel):
    code: str = Field(min_length=1, max_length=256)


class AdminContact(ApiModel):
    telegram_username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_]+$")


class LicenseCreate(ApiModel):
    duration_value: int | None = Field(default=None, ge=1, le=8760)
    duration_unit: Literal["hours", "days"] | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def valid_window(self):
        has_dates = self.start_date is not None or self.end_date is not None
        if has_dates:
            if not self.start_date or not self.end_date:
                raise ValueError("Both start date and end date are required.")
            if self.end_date < self.start_date:
                raise ValueError("End date must be on or after start date.")
        elif self.duration_value is None or self.duration_unit is None:
            raise ValueError("Provide a duration or a date range.")
        return self


class LicenseValidate(ApiModel):
    key: str = Field(min_length=12, max_length=12, pattern=r"^\d{12}$")
