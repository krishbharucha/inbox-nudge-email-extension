"""Authenticated Web Push subscriptions, encrypted at rest."""

import base64
import hashlib
import json
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from requests import Session as HTTPSession
from sqlalchemy import func, select

from .auth import Actor, Db
from .config import get_settings
from .google_http import encrypt
from .models import PushSubscription

router = APIRouter(prefix="/api/notifications")


class PushHTTPSession(HTTPSession):
    def request(self, *args, **kwargs):
        # A trusted push host must not redirect a POST to an arbitrary host.
        kwargs["allow_redirects"] = False
        return super().request(*args, **kwargs)


def endpoint_allowed(value):
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.port in {None, 443}
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
        and parsed.hostname
        in {
            "fcm.googleapis.com",
            "updates.push.services.mozilla.com",
            "web.push.apple.com",
        }
    )


class Keys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(max_length=100)
    auth: str = Field(max_length=50)


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=10, max_length=4096)
    keys: Keys
    expirationTime: float | None = None

    @model_validator(mode="after")
    def valid_subscription(self):
        if not endpoint_allowed(self.endpoint):
            raise ValueError("Unsupported browser push endpoint")
        try:
            auth = base64.urlsafe_b64decode(self.keys.auth + "=" * (-len(self.keys.auth) % 4))
            key = base64.urlsafe_b64decode(self.keys.p256dh + "=" * (-len(self.keys.p256dh) % 4))
            if len(auth) != 16 or len(key) != 65:
                raise ValueError()
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key)
        except (ValueError, TypeError):
            raise ValueError("Invalid push subscription keys") from None
        return self


@router.get("/config")
def config(actor: Actor, db: Db):
    settings = get_settings()
    count = db.scalar(
        select(func.count())
        .select_from(PushSubscription)
        .where(PushSubscription.user_id == actor.id, PushSubscription.active.is_(True))
    )
    return {
        "configured": settings.push_configured,
        "public_key": settings.vapid_public_key,
        "subscriptions": count,
        "web_app_origin": settings.web_app_origin,
    }


@router.post("/subscriptions")
def subscribe(body: SubscriptionInput, actor: Actor, db: Db):
    if actor.is_demo or not get_settings().push_configured:
        raise HTTPException(409, "Browser push is available for configured Google accounts")
    digest = hashlib.sha256(body.endpoint.encode()).hexdigest()
    value = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == digest))
    if value and value.user_id != actor.id:
        raise HTTPException(
            409,
            "This browser is registered to another account. Disable its notifications before switching accounts.",
        )
    if not value:
        count = db.scalar(
            select(func.count())
            .select_from(PushSubscription)
            .where(PushSubscription.user_id == actor.id)
        )
        if count >= 10:
            raise HTTPException(409, "Remove a registered browser before adding another")
        value = PushSubscription(user_id=actor.id, endpoint_hash=digest)
        db.add(value)
    value.subscription_encrypted = encrypt(json.dumps(body.model_dump()))
    value.active = True
    db.commit()
    return {"id": value.id, "active": True}


class SubscriptionDelete(BaseModel):
    endpoint: str = Field(max_length=4096)


@router.post("/subscriptions/remove")
def remove(body: SubscriptionDelete, actor: Actor, db: Db):
    value = db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == actor.id,
            PushSubscription.endpoint_hash == hashlib.sha256(body.endpoint.encode()).hexdigest(),
        )
    )
    if value:
        db.delete(value)
        db.commit()
    return {"removed": True}


@router.post("/subscriptions/remove-all")
def remove_all(actor: Actor, db: Db):
    from sqlalchemy import delete

    db.execute(delete(PushSubscription).where(PushSubscription.user_id == actor.id))
    db.commit()
    return {"removed": True}
