from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    app_env: Literal["development", "production"] = "development"
    web_auth_enabled: bool = True
    request_limits_enabled: bool = True
    database_url: str = Field(default="sqlite:///./inbox-nudge.db", repr=False, exclude=True)
    demo_mode: bool = False
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "inbox-nudge"
    jwt_audience: str = "inbox-nudge-api"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    extension_id: str = ""
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://127.0.0.1:8000/api/auth/google/callback"
    web_app_origin: str = "http://127.0.0.1:5173"
    token_encryption_key: SecretStr = SecretStr("")
    cooldown_hours: int = 24
    daily_limit: int = 10
    delivery_worker_enabled: bool = True
    vapid_private_key: SecretStr = SecretStr("")
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"

    @property
    def push_configured(self):
        return bool(self.vapid_private_key.get_secret_value() and self.vapid_public_key)

    @property
    def google_configured(self):
        return bool(
            self.google_client_id
            and self.google_client_secret.get_secret_value()
            and self.token_encryption_key.get_secret_value()
        )

    @model_validator(mode="after")
    def require_secret(self):
        secret = self.jwt_secret.get_secret_value()
        if len(secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        if self.google_configured and secret == "local-demo-only-change-this-secret-32chars":
            raise ValueError("Replace the public demo JWT_SECRET before connecting Google accounts")
        from urllib.parse import urlsplit

        for value in (self.google_redirect_uri, self.web_app_origin):
            parsed = urlsplit(value)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("OAuth URLs cannot contain credentials, queries, or fragments")
            if parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
            ):
                raise ValueError("OAuth URLs require HTTPS except on loopback")
        if self.extension_id and (
            len(self.extension_id) != 32
            or any(c not in "abcdefghijklmnop" for c in self.extension_id)
        ):
            raise ValueError("EXTENSION_ID must be the 32-letter ID from Chrome")
        if self.app_env == "production":
            import ipaddress

            from sqlalchemy.engine import make_url

            if (
                self.demo_mode
                or len(secret) < 48
                or secret == "local-demo-only-change-this-secret-32chars"
            ):
                raise ValueError(
                    "Production requires demo login disabled and a unique JWT secret of at least 48 characters"
                )
            if (
                not self.google_configured
                or not self.extension_id
                or not self.request_limits_enabled
            ):
                raise ValueError(
                    "Production requires Google credentials, the Store extension ID, and request limits"
                )
            for value in [
                self.google_redirect_uri,
                *([self.web_app_origin] if self.web_auth_enabled else []),
            ]:
                parsed = urlsplit(value)
                host = (parsed.hostname or "").lower().rstrip(".")
                try:
                    ipaddress.ip_address(host)
                    public_host = False
                except ValueError:
                    public_host = bool(
                        "." in host
                        and not any(
                            host == suffix or host.endswith("." + suffix)
                            for suffix in (
                                "localhost",
                                "local",
                                "internal",
                                "test",
                                "invalid",
                                "example",
                                "example.com",
                                "example.net",
                                "example.org",
                            )
                        )
                    )
                if parsed.scheme != "https" or not public_host:
                    raise ValueError("Production OAuth URLs require a public HTTPS domain")
            database = make_url(self.database_url)
            if (
                database.get_backend_name() != "postgresql"
                or database.query.get("sslmode") != "verify-full"
            ):
                raise ValueError(
                    "Production requires PostgreSQL with sslmode=verify-full and a trusted database CA"
                )
            if any(
                origin and (not origin.startswith("https://") or "*" in origin)
                for origin in self.cors_origins.split(",")
            ):
                raise ValueError("Production CORS origins must be explicit HTTPS origins or empty")
        if self.token_encryption_key.get_secret_value():
            from cryptography.fernet import Fernet

            Fernet(self.token_encryption_key.get_secret_value().encode())
        private = self.vapid_private_key.get_secret_value()
        if bool(private) != bool(self.vapid_public_key):
            raise ValueError("Configure both VAPID keys together")
        if private:
            import base64

            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec

            try:
                key = serialization.load_der_private_key(base64.urlsafe_b64decode(private), None)
                public = base64.urlsafe_b64decode(
                    self.vapid_public_key + "=" * (-len(self.vapid_public_key) % 4)
                )
                if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
                    key.curve, ec.SECP256R1
                ):
                    raise ValueError()
                if public != key.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                ):
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError(
                    "VAPID keys must be a matching P-256 pair; run app.setup_push"
                ) from None
            if not (
                self.vapid_subject.startswith("mailto:") and "@" in self.vapid_subject
            ) and not self.vapid_subject.startswith("https://"):
                raise ValueError("VAPID_SUBJECT must be a mailto contact or HTTPS URL")
        return self


@lru_cache
def get_settings():
    return Settings()
