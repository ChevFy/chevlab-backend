import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from core.config import settings

ph = PasswordHasher()


def verify_password(plain: str):
    try:
        return ph.verify(settings.ADMIN_PASSWORD_HASH, plain)
    except (VerificationError, InvalidHashError):
        return False


def authenticate(username: str, password: str):
    username_ok = secrets.compare_digest(username.encode(), settings.ADMIN_USERNAME.encode())
    password_ok = verify_password(password)
    return username_ok and password_ok
