"""FastAPI dependencies — JWT verification + ownership."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Cookie, HTTPException, Request, status
from jose import JWTError, jwt


@dataclass(frozen=True)
class User:
    id: str
    email: str


def _read_jwt_cookie(request: Request) -> Optional[str]:
    """Read the NextAuth session cookie. NextAuth uses different names by env:
    - prod (HTTPS): __Secure-next-auth.session-token
    - dev (HTTP):   next-auth.session-token
    Try both; pick the first non-empty.
    """
    for name in ("__Secure-next-auth.session-token", "next-auth.session-token",
                 "authjs.session-token", "__Secure-authjs.session-token"):
        v = request.cookies.get(name)
        if v:
            return v
    # Fallback: Authorization: Bearer for tests / API consumers.
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return None


def current_user(request: Request) -> User:
    secret = os.environ.get("NEXTAUTH_SECRET")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="NEXTAUTH_SECRET not configured",
        )
    token = _read_jwt_cookie(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    sub = payload.get("sub") or payload.get("id")
    email = payload.get("email") or ""
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return User(id=str(sub), email=str(email))
