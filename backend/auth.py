"""JWT token creation and verification."""

import time
from typing import Optional

import jwt

from config import settings as cfg


def create_token() -> str:
    payload = {
        "sub": "admin",
        "iat": int(time.time()),
        "exp": int(time.time()) + cfg.jwt_expire_days * 86400,
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm="HS256")


def verify_token(token: str) -> bool:
    try:
        jwt.decode(token, cfg.jwt_secret, algorithms=["HS256"])
        return True
    except Exception:
        return False
