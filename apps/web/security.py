"""Local-admin authentication, session-bound CSRF and bounded rate limiting."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

_PASSWORD_PREFIX = "scrypt-v1"
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 128 * 1024 * 1024
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


class AuthenticationError(ValueError):
    """Credentials or the signed owner session are invalid."""


class CsrfValidationError(ValueError):
    """A mutating request lacks its session-bound CSRF proof."""


class OriginValidationError(ValueError):
    """A browser request declares an origin outside the local allow-list."""


class RateLimitExceeded(RuntimeError):
    """A bounded request class exhausted its fixed-window allowance."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    subject: str
    nonce: str = field(repr=False)
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class WebSecurityConfig:
    admin_password_hash: str = field(repr=False)
    session_secret: str = field(repr=False)
    allowed_origins: tuple[str, ...]
    cookie_secure: bool
    session_ttl_seconds: int = 8 * 60 * 60
    cookie_name: str = "noezema_session"
    login_attempt_limit: int = 5
    command_request_limit: int = 60
    rate_window_seconds: int = 60

    def __post_init__(self) -> None:
        _PasswordVerifier.parse(self.admin_password_hash)
        if len(self.session_secret.encode("utf-8")) < 32:
            raise ValueError("web session secret must contain at least 32 bytes")
        if not self.allowed_origins:
            raise ValueError("at least one web origin must be allowed")
        normalized = tuple(_normalize_origin(item) for item in self.allowed_origins)
        if len(set(normalized)) != len(normalized):
            raise ValueError("allowed web origins must be unique")
        object.__setattr__(self, "allowed_origins", normalized)
        if not 300 <= self.session_ttl_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("web session TTL must be between 5 minutes and 7 days")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.cookie_name):
            raise ValueError("web cookie name is invalid")
        if self.login_attempt_limit < 1 or self.command_request_limit < 1:
            raise ValueError("web rate limits must be positive")
        if not 1 <= self.rate_window_seconds <= 60 * 60:
            raise ValueError("web rate-limit window must be between 1 second and 1 hour")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> WebSecurityConfig:
        values = environment if environment is not None else os.environ
        password_hash = _required(values, "NOEZEMA_WEB_ADMIN_PASSWORD_SCRYPT")
        session_secret = _required(values, "NOEZEMA_WEB_SESSION_SECRET")
        origins = tuple(
            item.strip()
            for item in _required(values, "NOEZEMA_WEB_ALLOWED_ORIGINS").split(",")
            if item.strip()
        )
        cookie_secure = _parse_bool(values.get("NOEZEMA_WEB_COOKIE_SECURE", "true"))
        return cls(
            admin_password_hash=password_hash,
            session_secret=session_secret,
            allowed_origins=origins,
            cookie_secure=cookie_secure,
        )


class AuthManager:
    """Verify the admin password and issue short-lived signed cookie sessions."""

    def __init__(
        self,
        config: WebSecurityConfig,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._password = _PasswordVerifier.parse(config.admin_password_hash)
        self._secret = config.session_secret.encode("utf-8")
        self._clock = clock or (lambda: datetime.now(UTC))

    def login(self, password: str) -> IssuedSession:
        if not self._password.verify(password):
            raise AuthenticationError("invalid credentials")
        now = _aware(self._clock()).astimezone(UTC)
        issued_at = int(now.timestamp())
        expires_at = issued_at + self.config.session_ttl_seconds
        nonce = secrets.token_urlsafe(24)
        payload = {
            "v": 1,
            "sub": "owner",
            "iat": issued_at,
            "exp": expires_at,
            "nonce": nonce,
        }
        encoded_payload = _b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = _b64encode(
            hmac.digest(self._secret, b"session:v1:" + encoded_payload.encode("ascii"), "sha256")
        )
        principal = AuthenticatedPrincipal(
            subject="owner",
            nonce=nonce,
            issued_at=datetime.fromtimestamp(issued_at, UTC),
            expires_at=datetime.fromtimestamp(expires_at, UTC),
        )
        return IssuedSession(
            token=f"{encoded_payload}.{signature}",
            csrf_token=self.csrf_token(principal),
            principal=principal,
        )

    def authenticate(self, token: str | None) -> AuthenticatedPrincipal:
        try:
            if token is None or not token or len(token) > 2048:
                raise ValueError
            encoded_payload, encoded_signature = token.split(".", maxsplit=1)
            supplied_signature = _b64decode(encoded_signature)
            expected_signature = hmac.digest(
                self._secret,
                b"session:v1:" + encoded_payload.encode("ascii"),
                "sha256",
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError
            payload = json.loads(_b64decode(encoded_payload))
            if not isinstance(payload, dict) or set(payload) != {
                "v",
                "sub",
                "iat",
                "exp",
                "nonce",
            }:
                raise ValueError
            if payload["v"] != 1 or payload["sub"] != "owner":
                raise ValueError
            if type(payload["iat"]) is not int or type(payload["exp"]) is not int:
                raise ValueError
            nonce = payload["nonce"]
            if not isinstance(nonce, str) or _NONCE_PATTERN.fullmatch(nonce) is None:
                raise ValueError
            now = int(_aware(self._clock()).timestamp())
            if payload["iat"] > now + 30 or payload["exp"] <= now:
                raise ValueError
            lifetime = payload["exp"] - payload["iat"]
            if not 0 < lifetime <= self.config.session_ttl_seconds:
                raise ValueError
        except (
            ValueError,
            TypeError,
            UnicodeError,
            json.JSONDecodeError,
            binascii.Error,
        ) as exc:
            raise AuthenticationError("invalid or expired owner session") from exc
        return AuthenticatedPrincipal(
            subject="owner",
            nonce=nonce,
            issued_at=datetime.fromtimestamp(payload["iat"], UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )

    def csrf_token(self, principal: AuthenticatedPrincipal) -> str:
        return _b64encode(
            hmac.digest(
                self._secret,
                f"csrf:v1:{principal.nonce}".encode(),
                "sha256",
            )
        )

    def verify_csrf(self, principal: AuthenticatedPrincipal, supplied_token: str | None) -> None:
        expected = self.csrf_token(principal)
        if (
            supplied_token is None
            or len(supplied_token) > 256
            or not hmac.compare_digest(supplied_token, expected)
        ):
            raise CsrfValidationError("invalid CSRF token")

    def verify_origin(self, supplied_origin: str | None) -> None:
        if supplied_origin is None:
            return
        try:
            normalized = _normalize_origin(supplied_origin)
        except ValueError as exc:
            raise OriginValidationError("request origin is not allowed") from exc
        if normalized not in self.config.allowed_origins:
            raise OriginValidationError("request origin is not allowed")


@dataclass(slots=True)
class _Window:
    count: int
    reset_at: float


class FixedWindowRateLimiter:
    """Small in-process limiter suitable for the single local web process."""

    def __init__(self, *, monotonic: Callable[[], float] | None = None) -> None:
        self._monotonic = monotonic or time.monotonic
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def consume(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = self._monotonic()
        with self._lock:
            current = self._windows.get(key)
            if current is None or now >= current.reset_at:
                self._windows[key] = _Window(count=1, reset_at=now + window_seconds)
                self._prune(now)
                return
            if current.count >= limit:
                raise RateLimitExceeded(max(1, math.ceil(current.reset_at - now)))
            current.count += 1

    def _prune(self, now: float) -> None:
        if len(self._windows) <= 1024:
            return
        expired = [key for key, window in self._windows.items() if window.reset_at <= now]
        for key in expired:
            self._windows.pop(key, None)


@dataclass(frozen=True, slots=True)
class _PasswordVerifier:
    n: int
    r: int
    p: int
    salt: bytes = field(repr=False)
    digest: bytes = field(repr=False)

    @classmethod
    def parse(cls, encoded: str) -> _PasswordVerifier:
        try:
            prefix, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$")
            n, r, p = int(n_text), int(r_text), int(p_text)
            salt, digest = _b64decode(salt_text), _b64decode(digest_text)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValueError("admin password hash is invalid") from exc
        if prefix != _PASSWORD_PREFIX:
            raise ValueError("admin password hash version is unsupported")
        if n < 2**14 or n > 2**20 or n & (n - 1) != 0:
            raise ValueError("admin password scrypt N is invalid")
        if not 1 <= r <= 32 or not 1 <= p <= 16:
            raise ValueError("admin password scrypt parameters are invalid")
        if not 16 <= len(salt) <= 64 or len(digest) != 32:
            raise ValueError("admin password hash material is invalid")
        return cls(n=n, r=r, p=p, salt=salt, digest=digest)

    def verify(self, password: str) -> bool:
        if not isinstance(password, str) or not 1 <= len(password) <= 1024:
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=self.salt,
            n=self.n,
            r=self.r,
            p=self.p,
            dklen=len(self.digest),
            maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(candidate, self.digest)


def hash_admin_password(password: str, *, salt: bytes | None = None) -> str:
    """Create the validated environment value used by the local admin login."""

    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("admin password must contain between 12 and 1024 characters")
    actual_salt = salt or secrets.token_bytes(24)
    if not 16 <= len(actual_salt) <= 64:
        raise ValueError("admin password salt must contain between 16 and 64 bytes")
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=actual_salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
        maxmem=_SCRYPT_MAXMEM,
    )
    return "$".join(
        (
            _PASSWORD_PREFIX,
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            _b64encode(actual_salt),
            _b64encode(digest),
        )
    )


def _normalize_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("web origin must be an absolute HTTP(S) origin")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("web origin must not contain user information")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("web origin must not contain a path, query or fragment")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key)
    if value is None or not value.strip():
        raise RuntimeError(f"{key} is required")
    return value.strip()


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError("NOEZEMA_WEB_COOKIE_SECURE must be a boolean")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value or len(value) > 4096:
        raise ValueError("invalid base64 value")
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    if _b64encode(decoded) != value:
        raise ValueError("base64 value is not canonical")
    return decoded


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("security clock must return a timezone-aware datetime")
    return value
