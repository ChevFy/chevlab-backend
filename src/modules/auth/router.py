from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from .jwt import create_access_token
from .security import authenticate

auth_router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@auth_router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    if not authenticate(body.username, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    return TokenResponse(access_token=create_access_token(body.username))
