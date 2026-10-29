from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = timedelta(hours=1)

bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(subject: str):
    now = datetime.now(UTC)
    payload = {"sub": subject, "iat": now, "exp": now + ACCESS_TOKEN_EXPIRE}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str):
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[ALGORITHM],
        options={"require": ["exp", "sub"]},
    )
    return payload["sub"]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise credentials_exception
    try:
        username = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise credentials_exception from None
    if username != settings.ADMIN_USERNAME:
        raise credentials_exception
    return username
