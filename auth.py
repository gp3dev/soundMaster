import hashlib
import secrets
import time
from typing import Optional

_SESSION_TTL = 8 * 3600  # 8 Stunden
_sessions: dict[str, float] = {}  # token → expiry_unix
_password_hash: Optional[str] = None


def init(password_hash: Optional[str]) -> None:
    global _password_hash
    _password_hash = password_hash


def is_configured() -> bool:
    return bool(_password_hash)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000).hex()
    return f"pbkdf2$sha256${salt}${h}"


def verify_password(password: str) -> bool:
    if not _password_hash:
        return False
    try:
        _, _algo, salt, expected = _password_hash.split("$")
        derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000).hex()
        return secrets.compare_digest(expected, derived)
    except Exception:
        return False


def create_session() -> str:
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + _SESSION_TTL
    return token


def validate_session(token: Optional[str]) -> bool:
    if not token or token not in _sessions:
        return False
    if time.time() > _sessions[token]:
        del _sessions[token]
        return False
    return True


def destroy_session(token: str) -> None:
    _sessions.pop(token, None)
