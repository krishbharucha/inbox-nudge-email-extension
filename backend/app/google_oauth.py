import base64
import hashlib
import logging
import re
import secrets
import ssl
from datetime import timedelta
from urllib.parse import urlencode

import certifi
import jwt
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from .auth import Actor, Db, issue_token
from .config import get_settings
from .google_http import (
    GMAIL_BASE,
    GMAIL_SCOPE,
    GMAIL_SEND_SCOPE,
    REVOKE_URL,
    SCOPES,
    GoogleUnavailable,
    decrypt,
    encrypt,
    google_request,
    token_request,
)
from .models import (
    AuditLog,
    ConnectedAccount,
    OAuthAttempt,
    PushSubscription,
    RecipientPreferences,
    User,
    now,
)

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
keys = jwt.PyJWKClient(
    "https://www.googleapis.com/oauth2/v3/certs",
    cache_keys=False,
    lifespan=300,
    timeout=10,
    ssl_context=ssl.create_default_context(cafile=certifi.where()),
)


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def challenge(value: str):
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()).rstrip(b"=").decode()


def require_google():
    if not get_settings().google_configured:
        raise HTTPException(
            503, "Google connection needs configuration. Follow docs/GMAIL_SETUP.md."
        )


def verify_identity(encoded: str, nonce: str):
    settings = get_settings()
    try:
        signing_key = keys.get_signing_key_from_jwt(encoded)
        claims = jwt.decode(
            encoded,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=["https://accounts.google.com", "accounts.google.com"],
            options={"require": ["exp", "iat", "sub", "email", "nonce"]},
        )
        if claims.get("email_verified") is not True or not secrets.compare_digest(
            claims["nonce"], nonce
        ):
            raise ValueError()
        if claims.get("azp", settings.google_client_id) != settings.google_client_id:
            raise ValueError()
        return claims
    except jwt.PyJWKClientConnectionError:
        raise GoogleUnavailable(
            "Unable to securely retrieve Google's signing certificates. Check the backend's internet connection and certificate trust."
        ) from None
    except (jwt.PyJWTError, ValueError, TypeError):
        raise GoogleUnavailable("Unable to verify your Google identity") from None


@router.get("/auth/google/start")
def start(db: Db, redirect_uri: str, code_challenge: str, email_reminders: bool = False):
    require_google()
    settings = get_settings()
    allowed = {settings.web_app_origin.rstrip("/") + "/"} if settings.web_auth_enabled else set()
    if settings.extension_id:
        allowed.add(f"https://{settings.extension_id}.chromiumapp.org/google")
    if redirect_uri not in allowed or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge):
        raise HTTPException(400, "Invalid OAuth return destination or PKCE challenge")
    state, verifier, nonce = (
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(48),
        secrets.token_urlsafe(32),
    )
    db.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at < now()))
    db.add(
        OAuthAttempt(
            state_hash=digest(state),
            verifier_encrypted=encrypt(verifier),
            nonce=nonce,
            redirect_uri=redirect_uri,
            app_challenge=code_challenge,
            expires_at=now() + timedelta(minutes=10),
        )
    )
    db.commit()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join([*SCOPES, GMAIL_SEND_SCOPE] if email_reminders else SCOPES),
            "state": state,
            "nonce": nonce,
            "access_type": "offline",
            "prompt": "consent select_account",
            "code_challenge": challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return RedirectResponse(
        url, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )


@router.get("/auth/google/callback")
def callback(db: Db, state: str, code: str | None = None, error: str | None = None):
    require_google()
    attempt = db.get(OAuthAttempt, digest(state))
    if not attempt:
        raise HTTPException(400, "Invalid or expired Google sign-in. Start again.")
    claimed = db.execute(
        update(OAuthAttempt)
        .where(
            OAuthAttempt.state_hash == attempt.state_hash,
            OAuthAttempt.status == "pending",
            OAuthAttempt.expires_at > now(),
        )
        .values(status="exchanging")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise HTTPException(400, "Invalid or expired Google sign-in. Start again.")
    db.commit()

    def redirect(**params):
        return RedirectResponse(
            attempt.redirect_uri + "?" + urlencode(params),
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    if error or not code:
        return redirect(oauth_error="Google sign-in was cancelled. Try again when you are ready.")
    stage = "token exchange"
    try:
        body = token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": get_settings().google_redirect_uri,
                "code_verifier": decrypt(attempt.verifier_encrypted),
            }
        )
        if GMAIL_SCOPE not in str(body.get("scope", "")).split():
            raise GoogleUnavailable("Please grant Gmail read-only access to connect Inbox Nudge")
        stage = "identity verification"
        claims = verify_identity(body.get("id_token", ""), attempt.nonce)
        email = str(claims["email"]).lower()
        stage = "Gmail profile verification"
        profile = google_request(
            "GET",
            f"{GMAIL_BASE}/profile",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        if profile.status_code != 200:
            try:
                error_body = profile.json().get("error", {})
                reasons = {item.get("reason") for item in error_body.get("errors", [])}
                reasons.update(item.get("reason") for item in error_body.get("details", []))
            except (ValueError, AttributeError, TypeError):
                reasons = set()
            if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
                raise GoogleUnavailable(
                    "Enable the Gmail API in the Google Cloud project that owns this OAuth client, then connect again."
                )
            if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
                raise GoogleUnavailable(
                    "Gmail read-only permission was not granted. Connect again and approve Gmail access."
                )
            raise GoogleUnavailable(
                "Google denied the Gmail mailbox check. Confirm Gmail is enabled and check your account's access policies."
            )
        if profile.json().get("emailAddress", "").lower() != email:
            raise GoogleUnavailable(
                "The Gmail mailbox does not match your verified Google identity"
            )
        stage = "account registration"
        account = db.scalar(
            select(ConnectedAccount).where(
                ConnectedAccount.provider == "google",
                ConnectedAccount.provider_account_id == claims["sub"],
            )
        )
        existing = db.scalar(select(User).where(User.email == email))
        if account:
            user = db.get(User, account.user_id)
            if user.email != email:
                raise GoogleUnavailable(
                    "Your Google account email has changed; contact support before reconnecting"
                )
        else:
            if existing:
                raise GoogleUnavailable(
                    "This address is already registered to a different identity"
                )
            user = User(
                email=email,
                display_name=str(claims.get("name", email.split("@")[0]))[:120],
                is_demo=False,
            )
            db.add(user)
            db.flush()
            account = ConnectedAccount(
                user_id=user.id, provider="google", provider_account_id=claims["sub"]
            )
            db.add(account)
            db.add(RecipientPreferences(user_id=user.id))
        refresh = body.get("refresh_token")
        if not refresh and not account.refresh_token_encrypted:
            raise GoogleUnavailable(
                "Google did not grant offline access. Reconnect and approve access again."
            )
        account.access_token_encrypted = encrypt(body["access_token"])
        if refresh:
            account.refresh_token_encrypted = encrypt(refresh)
        account.token_expires_at = now() + timedelta(seconds=int(body.get("expires_in", 3600)))
        account.granted_scopes = str(body["scope"]).split()
        account.connection_status, account.last_seen_at = "connected", now()
        exchange_code = secrets.token_urlsafe(32)
        attempt.exchange_hash, attempt.user_id = digest(exchange_code), user.id
        attempt.status, attempt.expires_at = "authorized", now() + timedelta(minutes=1)
        attempt.verifier_encrypted = "consumed"
        db.add(
            AuditLog(user_id=user.id, action="google.connected", resource_id=account.id or user.id)
        )
        db.commit()
        return redirect(oauth_code=exchange_code)
    except GoogleUnavailable as failure:
        db.rollback()
        # Log only the stage/type, never Google responses, codes, tokens, or PII.
        logger.warning("Google connection failed at %s (%s)", stage, type(failure).__name__)
        return redirect(oauth_error=str(failure))
    except (ValueError, TypeError, KeyError, IntegrityError) as failure:
        db.rollback()
        logger.warning("Google connection failed at %s (%s)", stage, type(failure).__name__)
        return redirect(
            oauth_error="Google returned an unexpected connection response. Please start Google sign-in again."
        )


class Exchange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=40, max_length=100)
    verifier: str = Field(pattern=r"^[A-Za-z0-9._~-]{43,128}$")
    installation_id: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )


@router.post("/auth/google/exchange")
def exchange(body: Exchange, db: Db, response: Response):
    require_google()
    attempt = db.scalar(select(OAuthAttempt).where(OAuthAttempt.exchange_hash == digest(body.code)))
    if not attempt or not secrets.compare_digest(attempt.app_challenge, challenge(body.verifier)):
        raise HTTPException(400, "Invalid or expired sign-in code")
    extension_flow = bool(
        get_settings().extension_id
        and attempt.redirect_uri == f"https://{get_settings().extension_id}.chromiumapp.org/google"
    )
    if extension_flow and not body.installation_id:
        raise HTTPException(400, "Restart sign-in from the extension")
    result = db.execute(
        update(OAuthAttempt)
        .where(
            OAuthAttempt.state_hash == attempt.state_hash,
            OAuthAttempt.status == "authorized",
            OAuthAttempt.expires_at > now(),
        )
        .values(status="used", exchange_hash=None)
        .execution_options(synchronize_session=False)
    )
    user = db.get(User, attempt.user_id)
    if result.rowcount != 1 or not user or user.is_demo:
        raise HTTPException(400, "Invalid or expired sign-in code")
    tokens = {"access_token": issue_token(user), "token_type": "bearer", "expires_in": 3600}
    if extension_flow:
        from .extension_sessions import start_session

        tokens = start_session(db, user, body.installation_id)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return tokens


@router.post("/accounts/disconnect")
def disconnect(actor: Actor, db: Db):
    # Match send's user -> preferences -> credentials lock order.
    db.scalar(select(User).where(User.id == actor.id).with_for_update(key_share=True))
    prefs = db.scalar(
        select(RecipientPreferences)
        .where(RecipientPreferences.user_id == actor.id)
        .with_for_update()
    )
    account = db.scalar(
        select(ConnectedAccount).where(ConnectedAccount.user_id == actor.id).with_for_update()
    )
    revoked = False
    if account and account.provider == "google":
        stored = account.refresh_token_encrypted or account.access_token_encrypted
        if stored:
            try:
                result = google_request("POST", REVOKE_URL, data={"token": decrypt(stored)})
                revoked = result.status_code == 200
            except GoogleUnavailable:
                pass
        account.access_token_encrypted = account.refresh_token_encrypted = None
        account.token_expires_at = None
        account.granted_scopes = []
    if account:
        account.connection_status = "disconnected"
    db.execute(
        update(OAuthAttempt)
        .where(OAuthAttempt.user_id == actor.id)
        .values(status="used", exchange_hash=None)
    )
    actor.session_version += 1
    from .models import ExtensionInstallation, ExtensionSession

    db.execute(delete(ExtensionInstallation).where(ExtensionInstallation.user_id == actor.id))
    db.execute(delete(ExtensionSession).where(ExtensionSession.user_id == actor.id))
    db.execute(
        update(PushSubscription).where(PushSubscription.user_id == actor.id).values(active=False)
    )
    if prefs:
        prefs.reminders_enabled = False
    db.add(AuditLog(user_id=actor.id, action="account.disconnected", resource_id=actor.id))
    db.commit()
    return {"disconnected": True, "google_revoked": revoked}
