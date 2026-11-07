from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import ExtensionSession, User, now

bearer = HTTPBearer(auto_error=False)


def issue_token(user: User, extension_family_id: str | None = None):
    settings = get_settings()
    return jwt.encode(
        {
            "sub": user.id,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now(),
            "exp": now() + timedelta(hours=1),
            "demo": user.is_demo,
            "sv": user.session_version,
            **({"ef": extension_family_id} if extension_family_id else {}),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
):
    settings = get_settings()
    if not credentials:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        user = db.get(User, claims["sub"])
        if (
            not user
            or (user.is_demo and not settings.demo_mode)
            or claims.get("sv", 0) != user.session_version
        ):
            raise ValueError("User not available")
        if "ef" in claims and not db.scalar(
            select(ExtensionSession.token_hash)
            .where(
                ExtensionSession.family_id == claims["ef"],
                ExtensionSession.user_id == user.id,
                ExtensionSession.consumed_at.is_(None),
                ExtensionSession.expires_at > now(),
                ExtensionSession.session_version == user.session_version,
            )
            .limit(1)
        ):
            raise ValueError("Extension session revoked")
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(
            401, "Session expired or invalid", headers={"WWW-Authenticate": "Bearer"}
        )
    return user


Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[User, Depends(current_user)]
