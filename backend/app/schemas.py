from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Status = Literal[
    "delivered",
    "recipient_not_connected",
    "cooldown_active",
    "recipient_disabled",
    "thread_replied",
    "unable_to_verify",
    "rate_limited",
    "dismissed",
    "email_ready",
    "email_permission_required",
    "email_pending",
    "email_sending",
    "email_sent",
    "email_failed",
    "email_unknown",
]


class ReminderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=100)
    delivery_channel: Literal["in_app", "email"] = "in_app"


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    reminders_enabled: bool
    notification_enabled: bool
    blocked_senders: list[EmailStr] = Field(default_factory=list, max_length=200)


class DemoLogin(BaseModel):
    persona: Literal["alex", "maya"] = "alex"
