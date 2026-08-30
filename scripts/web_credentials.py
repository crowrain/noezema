"""Generate local-admin environment secrets without storing a plaintext password."""

from __future__ import annotations

import argparse
import getpass
import secrets
from urllib.parse import urlsplit

from apps.web.security import WebSecurityConfig, hash_admin_password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--origin",
        default="http://127.0.0.1:8000",
        help="exact browser origin used by the local web UI",
    )
    args = parser.parse_args()
    password = getpass.getpass("NOEZEMA admin password: ")
    confirmation = getpass.getpass("Repeat password: ")
    if password != confirmation:
        raise SystemExit("passwords do not match")
    password_hash = hash_admin_password(password)
    cookie_secure = urlsplit(args.origin).scheme.lower() == "https"
    session_secret = secrets.token_urlsafe(48)
    config = WebSecurityConfig(
        admin_password_hash=password_hash,
        session_secret=session_secret,
        allowed_origins=(args.origin,),
        cookie_secure=cookie_secure,
    )
    print(f"NOEZEMA_WEB_ADMIN_PASSWORD_SCRYPT={config.admin_password_hash}")
    print(f"NOEZEMA_WEB_SESSION_SECRET={config.session_secret}")
    print(f"NOEZEMA_WEB_ALLOWED_ORIGINS={config.allowed_origins[0]}")
    print(f"NOEZEMA_WEB_COOKIE_SECURE={str(config.cookie_secure).lower()}")


if __name__ == "__main__":
    main()
