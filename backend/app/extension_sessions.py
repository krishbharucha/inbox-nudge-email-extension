"""Opaque application sessions; Google credentials never enter the extension."""

import hashlib
import secrets
from datetime import UTC, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, text

from .auth import Db, issue_token
from .models import ConnectedAccount, ExtensionInstallation, ExtensionSession, User, now

router = APIRouter(prefix="/api/auth/extension")


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db, user, installation_id, family_id=None, expires_at=None):
    # Absolute lifetime: rotation never extends a family beyond thirty days.
    token = secrets.token_urlsafe(48)
    expires_at = (expires_at or now() + timedelta(days=30)).replace(tzinfo=UTC)
    if not family_id:
        db.scalar(select(User).where(User.id == user.id).with_for_update(key_share=True))
        db.execute(delete(ExtensionSession).where(ExtensionSession.expires_at < now()))
        db.execute(
            delete(ExtensionSession).where(
                ExtensionSession.user_id == user.id,
                ExtensionSession.installation_id == installation_id,
            )
        )
    family_id = family_id or str(uuid4())
    db.add(
        ExtensionSession(
            token_hash=digest(token),
            family_id=family_id,
            user_id=user.id,
            installation_id=installation_id,
            session_version=user.session_version,
            expires_at=expires_at,
        )
    )
    return {
        "access_token": issue_token(user, extension_family_id=family_id),
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": token,
        "refresh_expires_at": expires_at,
    }


class Refresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=40, max_length=100)
    installation_id: UUID


@router.post("/refresh")
def refresh(body: Refresh, db: Db, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    row = db.get(ExtensionSession, digest(body.refresh_token))
    if not row:
        raise HTTPException(
            401, "Reconnect Gmail in the extension", headers={"Cache-Control": "no-store"}
        )
    # Global lock order is user -> session, matching logout/disconnect. A
    # concurrent rotation cannot create a successor after logout's DELETE.
    user = db.scalar(select(User).where(User.id == row.user_id).with_for_update(key_share=True))
    row = db.scalar(
        select(ExtensionSession)
        .where(ExtensionSession.token_hash == digest(body.refresh_token))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not row:
        raise HTTPException(
            401, "Reconnect Gmail in the extension", headers={"Cache-Control": "no-store"}
        )
    # A replay or invalid device binding revokes the entire rotation family.
    account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == row.user_id))
    if (
        row.consumed_at
        or row.expires_at.replace(tzinfo=UTC) <= now()
        or row.installation_id != str(body.installation_id)
        or not user
        or user.is_demo
        or row.session_version != user.session_version
        or not account
        or account.connection_status != "connected"
        or account.provider != "google"
    ):
        db.execute(delete(ExtensionSession).where(ExtensionSession.family_id == row.family_id))
        db.commit()
        raise HTTPException(
            401, "Reconnect Gmail in the extension", headers={"Cache-Control": "no-store"}
        )
    row.consumed_at = now()
    result = start_session(db, user, row.installation_id, row.family_id, row.expires_at)
    db.commit()
    return result


@router.post("/logout")
def logout(body: Refresh, db: Db, response: Response):
    response.headers["Cache-Control"] = "no-store"
    row = db.get(ExtensionSession, digest(body.refresh_token))
    if row and row.installation_id == str(body.installation_id):
        db.scalar(select(User).where(User.id == row.user_id).with_for_update(key_share=True))
        db.execute(delete(ExtensionSession).where(ExtensionSession.family_id == row.family_id))
        db.execute(
            delete(ExtensionInstallation).where(
                ExtensionInstallation.id == row.installation_id,
                ExtensionInstallation.user_id == row.user_id,
            )
        )
        db.commit()
    return {"signed_out": True}
